"""Tiny HTTP server for Grove. Standard library only, binds to localhost."""
import json
import mimetypes
import os
import posixpath
import re
import subprocess
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import metadata
from .library import Library

STATIC = os.path.join(os.path.dirname(__file__), "static")
mimetypes.add_type("application/epub+zip", ".epub")
mimetypes.add_type("application/x-mobipocket-ebook", ".mobi")
mimetypes.add_type("application/x-fictionbook+xml", ".fb2")
mimetypes.add_type("application/xhtml+xml", ".xhtml")
mimetypes.add_type("image/vnd.djvu", ".djvu")
mimetypes.add_type("text/markdown", ".md")

READER_CSS = """
html,body{background:transparent!important}
body{font-family:Georgia,'Iowan Old Style','Palatino Linotype',serif!important;line-height:1.7!important;
max-width:42em;margin:0 auto!important;padding:2.5em 1.6em 5em!important;color:var(--ink,#2c3a2e)!important}
img,svg{max-width:100%!important;height:auto!important}
a{color:#4f7a4a}
"""


def _open_external(path):
    for cmd in (["xdg-open", path], ["gio", "open", path]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except FileNotFoundError:
            continue
    return False


class Handler(BaseHTTPRequestHandler):
    lib: Library = None
    server_version = "Grove/1.0"

    def log_message(self, *a):  # quiet
        pass

    # ------------------------------------------------------------ helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n) or b"{}")

    def _bytes(self, data, ctype, cache=True):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cache:
            self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, ctype=None, inline=True):
        """Stream a file, honouring Range so PDF viewers can seek."""
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        disp = "inline" if inline else "attachment"
        from urllib.parse import quote
        self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{quote(os.path.basename(path))}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def _static(self, rel):
        rel = posixpath.normpath(rel).lstrip("/")
        if rel in ("", "."):
            rel = "index.html"
        path = os.path.join(STATIC, rel)
        if not os.path.abspath(path).startswith(os.path.abspath(STATIC)) or not os.path.isfile(path):
            return self._err(404, "not found")
        with open(path, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._bytes(data, ctype, cache=False)

    def _book(self, book_id):
        b = self.lib.get(int(book_id))
        if not b:
            self._err(404, "no such book")
        return b

    # ------------------------------------------------------------ routes
    def do_GET(self):
        url = urlparse(self.path)
        p = url.path
        q = parse_qs(url.query)
        try:
            if not p.startswith("/api/"):
                return self._static(p)
            if p == "/api/books":
                return self._json({"books": self.lib.all_books(), "scan": self.lib.scan_state})
            if p == "/api/settings":
                return self._json(self.lib.settings())
            if p == "/api/scan":
                return self._json(self.lib.scan_state)
            if p == "/api/organize/preview":
                ids = [int(i) for i in q.get("ids", [""])[0].split(",") if i]
                return self._json({"plan": self.lib.plan_organize(ids or None),
                                   "root": self.lib.settings()["library_root"]})
            m = re.match(r"^/api/books/(\d+)(/.*)?$", p)
            if m:
                b = self._book(m.group(1))
                if not b:
                    return
                sub = m.group(2) or ""
                if sub == "":
                    return self._json(b)
                if sub == "/cover":
                    cp = self.lib.cover_path(b["id"])
                    return self._file(cp, "image/jpeg") if cp else self._err(404, "no cover")
                if sub.startswith("/file"):
                    if not os.path.exists(b["path"]):
                        return self._err(404, "file is missing on disk")
                    if "download" not in q:
                        self.lib.mark_opened(b["id"])
                    return self._file(b["path"], inline="download" not in q)
                if sub == "/epub":
                    return self._epub_toc(b)
                if sub.startswith("/epub/"):
                    return self._epub_part(b, unquote(sub[len("/epub/"):]))
                if sub == "/text":
                    return self._text(b)
            return self._err(404, "unknown endpoint")
        except Exception as e:  # never crash the server
            try:
                self._err(500, str(e))
            except Exception:
                pass

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            if p == "/api/scan":
                if not self.lib.scan_state["running"]:
                    threading.Thread(target=self.lib.scan, daemon=True).start()
                    time.sleep(0.05)
                return self._json(self.lib.scan_state)
            if p == "/api/settings":
                s = self.lib.save_settings(self._body())
                threading.Thread(target=self.lib.scan, daemon=True).start()
                return self._json(s)
            if p == "/api/organize":
                body = self._body()
                return self._json(self.lib.apply_organize(body.get("ids") or None))
            if p == "/api/upload":
                name = unquote(self.headers.get("X-Filename", "upload"))
                n = int(self.headers.get("Content-Length") or 0)
                return self._json(self.lib.import_file(name, self.rfile, n))
            if p == "/api/bulk":
                body = self._body()
                fields = body.get("fields", {})
                for i in body.get("ids", []):
                    if "add_tags" in body:
                        b = self.lib.get(int(i))
                        if b:
                            self.lib.update(int(i), {"tags": b["tags"] + body["add_tags"]})
                    if fields:
                        self.lib.update(int(i), fields)
                return self._json({"ok": True})
            m = re.match(r"^/api/books/(\d+)/(\w+)$", p)
            if m:
                b = self._book(m.group(1))
                if not b:
                    return
                action = m.group(2)
                if action == "update":
                    return self._json(self.lib.update(b["id"], self._body()))
                if action == "open":
                    self.lib.mark_opened(b["id"])
                    return self._json({"ok": _open_external(b["path"])})
                if action == "reveal":
                    return self._json({"ok": _open_external(os.path.dirname(b["path"]))})
                if action == "refresh":
                    return self._json(self.lib.refresh_metadata(b["id"]))
                if action == "forget":
                    self.lib.forget(b["id"])
                    return self._json({"ok": True})
                if action == "trash":
                    return self._json({"ok": self.lib.trash(b["id"])})
            return self._err(404, "unknown endpoint")
        except ValueError as e:
            self._err(400, str(e))
        except Exception as e:
            self._err(500, str(e))

    # ------------------------------------------------------------ readers
    def _epub_toc(self, b):
        z, opf_dir, manifest, spine, _meta = metadata.epub_package(b["path"])
        z.close()
        chapters = []
        for idref in spine:
            if idref in manifest:
                href = manifest[idref][0]
                chapters.append(metadata._join(opf_dir, href))
        toc = metadata.epub_toc_labels(b["path"])
        labels, last = [], None
        for c in chapters:  # untitled parts continue the previous chapter's name
            if c in toc:
                last = toc[c]
                labels.append(last)
            else:
                labels.append(f"{last} (cont.)" if last else None)
        self.lib.mark_opened(b["id"])
        self._json({"chapters": chapters, "labels": labels, "css": READER_CSS})

    def _epub_part(self, b, inner):
        inner = posixpath.normpath(inner).lstrip("/")
        with zipfile.ZipFile(b["path"]) as z:
            try:
                data = z.read(inner)
            except KeyError:
                # some epubs have case/encoding mismatches; try a lenient match
                low = {n.lower(): n for n in z.namelist()}
                if inner.lower() not in low:
                    return self._err(404, "not in epub")
                data = z.read(low[inner.lower()])
        ctype = mimetypes.guess_type(inner)[0] or "application/octet-stream"
        if ctype == "application/xhtml+xml":
            ctype = "application/xhtml+xml; charset=utf-8"
        self._bytes(data, ctype)

    def _text(self, b):
        with open(b["path"], "rb") as f:
            raw = f.read(5 * 1024 * 1024)
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        self.lib.mark_opened(b["id"])
        self._json({"text": text})


def watcher(lib: Library):
    """Background rescan so new files dropped into your folders just appear."""
    while True:
        try:
            lib.scan()
        except Exception:
            pass
        time.sleep(lib.settings().get("scan_interval", 60))


def serve(host="127.0.0.1", port=8642, open_browser=True):
    lib = Library()
    Handler.lib = lib
    httpd = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=watcher, args=(lib,), daemon=True).start()
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    print(f"\n  🌲 Grove is growing at {url}\n     watching: {', '.join(lib.settings()['folders'])}\n     Ctrl+C to stop\n")
    if open_browser:
        threading.Timer(0.6, lambda: _open_external(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  🍃 bye")
