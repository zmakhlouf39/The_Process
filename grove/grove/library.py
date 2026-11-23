"""The library: a SQLite index of every book/document found in the watched folders.

Grove doesn't care *where* a file lives. It finds every file in a format it
recognises, fingerprints it, and keeps your notes/ratings/progress attached to
that fingerprint, so moving or renaming a file never loses anything.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from urllib.parse import quote

from . import metadata

DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "grove")

DEFAULT_SETTINGS = {
    "folders": [os.path.expanduser("~/Books")],
    "library_root": os.path.expanduser("~/Books"),
    "organize_pattern": "{category}/{author}/{title}",
    "scan_interval": 60,
    "theme": "light",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE,
    filename TEXT,
    ext TEXT,
    format TEXT,
    category TEXT,
    size INTEGER,
    mtime REAL,
    fingerprint TEXT,
    title TEXT,
    author TEXT,
    series TEXT DEFAULT '',
    year TEXT DEFAULT '',
    publisher TEXT DEFAULT '',
    language TEXT DEFAULT '',
    description TEXT DEFAULT '',
    pages INTEGER,
    tags TEXT DEFAULT '[]',
    status TEXT DEFAULT 'unread',
    rating INTEGER DEFAULT 0,
    favorite INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    progress TEXT DEFAULT '',
    has_cover INTEGER DEFAULT 0,
    missing INTEGER DEFAULT 0,
    added_at REAL,
    last_opened REAL
);
CREATE INDEX IF NOT EXISTS idx_fp ON books(fingerprint);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

EDITABLE = {"title", "author", "series", "year", "publisher", "language", "description",
            "tags", "status", "rating", "favorite", "notes", "progress"}


def fingerprint(path, size):
    """Fast content fingerprint: size + first and last 256 KB."""
    h = hashlib.sha1(str(size).encode())
    chunk = 256 * 1024
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, os.SEEK_END)
            h.update(f.read(chunk))
    return h.hexdigest()


def _safe_name(s, fallback="Unknown"):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", s or "").strip(" .")
    s = re.sub(r"\s+", " ", s)
    return (s[:120] or fallback)


class Library:
    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = data_dir
        self.cover_dir = os.path.join(data_dir, "covers")
        os.makedirs(self.cover_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(data_dir, "grove.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.scan_state = {"running": False, "done": 0, "found": 0, "added": 0, "last": None, "current": ""}
        for folder in self.settings()["folders"]:
            if folder == DEFAULT_SETTINGS["folders"][0]:
                os.makedirs(folder, exist_ok=True)

    # ---------------------------------------------------------------- settings
    def settings(self):
        with self.lock:
            rows = self.db.execute("SELECT key, value FROM settings").fetchall()
        s = dict(DEFAULT_SETTINGS)
        for r in rows:
            try:
                s[r["key"]] = json.loads(r["value"])
            except Exception:
                pass
        return s

    def save_settings(self, new):
        allowed = set(DEFAULT_SETTINGS)
        with self.lock:
            for k, v in new.items():
                if k not in allowed:
                    continue
                if k == "folders":
                    v = [os.path.abspath(os.path.expanduser(p.strip())) for p in v if p and p.strip()]
                    v = list(dict.fromkeys(v))
                if k == "library_root":
                    v = os.path.abspath(os.path.expanduser(v.strip()))
                if k == "scan_interval":
                    v = max(10, int(v))
                self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (k, json.dumps(v)))
            self.db.commit()
        return self.settings()

    # ---------------------------------------------------------------- queries
    def _row(self, r):
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        try:
            d["progress"] = json.loads(d["progress"]) if d.get("progress") else None
        except Exception:
            d["progress"] = None
        d["folder"] = os.path.dirname(d["path"])
        d["cover_url"] = f"/api/books/{d['id']}/cover?v={int(d['mtime'] or 0)}" if d["has_cover"] else None
        d["file_url"] = f"/api/books/{d['id']}/file/{quote(d['filename'])}"
        return d

    def all_books(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM books ORDER BY title COLLATE NOCASE").fetchall()
        books = [self._row(r) for r in rows]
        counts = {}
        for b in books:
            if b["fingerprint"] and not b["missing"]:
                counts[b["fingerprint"]] = counts.get(b["fingerprint"], 0) + 1
        for b in books:
            b["duplicate"] = counts.get(b["fingerprint"], 0) > 1
        return books

    def get(self, book_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        return self._row(r) if r else None

    def update(self, book_id, fields):
        sets, vals = [], []
        for k, v in fields.items():
            if k not in EDITABLE:
                continue
            if k == "tags":
                if isinstance(v, str):
                    v = [t.strip() for t in v.split(",")]
                v = json.dumps([t for t in dict.fromkeys(str(t).strip() for t in v) if t])
            elif k == "progress":
                v = json.dumps(v) if v is not None else ""
            elif k in ("rating", "favorite"):
                v = int(v or 0)
            elif k == "status" and v not in ("unread", "reading", "finished"):
                continue
            sets.append(f"{k}=?")
            vals.append(v)
        if sets:
            with self.lock:
                self.db.execute(f"UPDATE books SET {', '.join(sets)} WHERE id=?", (*vals, book_id))
                self.db.commit()
        return self.get(book_id)

    def mark_opened(self, book_id):
        with self.lock:
            self.db.execute(
                "UPDATE books SET last_opened=?, status=CASE WHEN status='unread' THEN 'reading' ELSE status END WHERE id=?",
                (time.time(), book_id))
            self.db.commit()

    def forget(self, book_id):
        with self.lock:
            self.db.execute("DELETE FROM books WHERE id=?", (book_id,))
            self.db.commit()
        cover = os.path.join(self.cover_dir, f"{book_id}.jpg")
        if os.path.exists(cover):
            os.remove(cover)

    def cover_path(self, book_id):
        p = os.path.join(self.cover_dir, f"{book_id}.jpg")
        return p if os.path.exists(p) else None

    # ---------------------------------------------------------------- scanning
    def _walk(self, folders):
        seen = set()
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            for root, dirs, files in os.walk(folder, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__MACOSX"]
                for name in files:
                    p = os.path.join(root, name)
                    if p in seen or not metadata.is_supported(p):
                        continue
                    seen.add(p)
                    yield p

    def scan(self):
        """Index new files, update changed ones, detect moves and missing files."""
        if not self.scan_lock.acquire(blocking=False):
            return self.scan_state
        try:
            st = self.scan_state
            st.update(running=True, done=0, found=0, added=0, current="")
            folders = self.settings()["folders"]
            with self.lock:
                known = {r["path"]: r for r in self.db.execute(
                    "SELECT id, path, size, mtime, fingerprint, missing FROM books")}
            paths = list(self._walk(folders))
            st["found"] = len(paths)
            present = set(paths)

            # records whose file vanished -> candidates for "it was moved"
            gone = {}
            for p, r in known.items():
                if p not in present and not os.path.exists(p):
                    gone.setdefault(r["fingerprint"], []).append(r)

            for p in paths:
                st["done"] += 1
                st["current"] = os.path.basename(p)
                try:
                    stat = os.stat(p)
                except OSError:
                    continue
                r = known.get(p)
                if r and r["size"] == stat.st_size and abs((r["mtime"] or 0) - stat.st_mtime) < 1:
                    if r["missing"]:
                        with self.lock:
                            self.db.execute("UPDATE books SET missing=0 WHERE id=?", (r["id"],))
                    continue
                try:
                    fp = fingerprint(p, stat.st_size)
                except OSError:
                    continue
                if r:  # file changed in place: refresh technical fields only
                    with self.lock:
                        self.db.execute("UPDATE books SET size=?, mtime=?, fingerprint=?, missing=0 WHERE id=?",
                                        (stat.st_size, stat.st_mtime, fp, r["id"]))
                    continue
                moved = gone.get(fp)
                if moved:  # same content, new place: keep everything the user set
                    old = moved.pop(0)
                    if not moved:
                        gone.pop(fp)
                    with self.lock:
                        self.db.execute(
                            "UPDATE books SET path=?, filename=?, mtime=?, missing=0 WHERE id=?",
                            (p, os.path.basename(p), stat.st_mtime, old["id"]))
                    continue
                self._add(p, stat, fp)
                st["added"] += 1
                if st["added"] % 20 == 0:
                    with self.lock:
                        self.db.commit()

            # whatever is still gone is missing (could be an unplugged drive)
            with self.lock:
                for rows in gone.values():
                    for r in rows:
                        self.db.execute("UPDATE books SET missing=1 WHERE id=?", (r["id"],))
                self.db.commit()
            st["last"] = time.time()
            return st
        finally:
            self.scan_state["running"] = False
            self.scan_state["current"] = ""
            self.scan_lock.release()

    def _add(self, path, stat, fp):
        info, cover = metadata.extract(path)
        with self.lock:
            cur = self.db.execute(
                """INSERT INTO books (path, filename, ext, format, category, size, mtime, fingerprint,
                   title, author, series, year, publisher, language, description, pages, tags, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (path, os.path.basename(path), metadata.ext_of(path), info["format"], info["category"],
                 stat.st_size, stat.st_mtime, fp, info.get("title", ""), info.get("author", ""),
                 info.get("series", ""), info.get("year", "") or "", info.get("publisher", ""),
                 info.get("language", ""), (info.get("description") or "")[:4000], info.get("pages"),
                 json.dumps(info.get("tags", [])), time.time()))
            book_id = cur.lastrowid
        if cover and metadata.save_cover(cover, os.path.join(self.cover_dir, f"{book_id}.jpg")):
            with self.lock:
                self.db.execute("UPDATE books SET has_cover=1 WHERE id=?", (book_id,))
        return book_id

    def refresh_metadata(self, book_id):
        """Re-read metadata from the file (overwrites title/author/etc)."""
        b = self.get(book_id)
        if not b or not os.path.exists(b["path"]):
            return b
        info, cover = metadata.extract(b["path"])
        fields = {k: info.get(k, "") for k in ("title", "author", "series", "year", "publisher", "language", "description")}
        fields["tags"] = info.get("tags", [])
        self.update(book_id, fields)
        if cover and metadata.save_cover(cover, os.path.join(self.cover_dir, f"{book_id}.jpg")):
            with self.lock:
                self.db.execute("UPDATE books SET has_cover=1, pages=? WHERE id=?", (info.get("pages"), book_id))
                self.db.commit()
        return self.get(book_id)

    # ---------------------------------------------------------------- importing
    def import_file(self, filename, stream, length):
        """Save an uploaded file into <library_root>/Inbox and index it."""
        root = self.settings()["library_root"]
        inbox = os.path.join(root, "Inbox")
        os.makedirs(inbox, exist_ok=True)
        name = _safe_name(os.path.basename(filename), "upload")
        if not metadata.is_supported(name):
            raise ValueError(f"Unsupported file type: {name}")
        dest = self._unique(os.path.join(inbox, name))
        remaining = length
        with open(dest, "wb") as f:
            while remaining > 0:
                chunk = stream.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        self._ensure_folder_watched(root)
        stat = os.stat(dest)
        fp = fingerprint(dest, stat.st_size)
        return self.get(self._add(dest, stat, fp))

    def _ensure_folder_watched(self, folder):
        s = self.settings()
        folder = os.path.abspath(folder)
        if not any(folder == f or folder.startswith(f.rstrip("/") + "/") for f in s["folders"]):
            self.save_settings({"folders": s["folders"] + [folder]})

    @staticmethod
    def _unique(path):
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        i = 2
        while os.path.exists(f"{base} ({i}){ext}"):
            i += 1
        return f"{base} ({i}){ext}"

    # ---------------------------------------------------------------- organizing
    def plan_organize(self, ids=None):
        """Work out where each book would go under the library root."""
        s = self.settings()
        root, pattern = s["library_root"], s["organize_pattern"] or DEFAULT_SETTINGS["organize_pattern"]
        plan = []
        targets = set()
        for b in self.all_books():
            if b["missing"] or (ids and b["id"] not in ids):
                continue
            first_author = re.split(r"\s*(?:&|;| and )\s*", b["author"] or "")[0]
            letter = (first_author or b["title"] or "#")[0].upper()
            values = {
                "author": _safe_name(first_author, "Unknown Author"),
                "title": _safe_name(b["title"], "Untitled"),
                "format": _safe_name(b["format"]),
                "category": _safe_name(b["category"] + "s" if b["category"] in ("Ebook", "Document", "Comic") else b["category"]),
                "series": _safe_name(b["series"], "No Series"),
                "year": _safe_name(b["year"], "Unknown Year"),
                "letter": letter if letter.isalpha() else "#",
                "language": _safe_name(b["language"], "Unknown"),
            }
            try:
                rel = pattern.format(**values)
            except (KeyError, IndexError, ValueError):
                rel = DEFAULT_SETTINGS["organize_pattern"].format(**values)
            rel = "/".join(_safe_name(part) for part in rel.split("/") if part.strip())
            dest = os.path.join(root, rel + "." + b["ext"])
            if os.path.abspath(dest) == os.path.abspath(b["path"]):
                continue
            # avoid collisions with other files and other planned moves
            base, ext = os.path.splitext(dest)
            i = 2
            while dest in targets or (os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(b["path"])):
                dest = f"{base} ({i}){ext}"
                i += 1
            targets.add(dest)
            plan.append({"id": b["id"], "title": b["title"], "from": b["path"], "to": dest})
        return plan

    def apply_organize(self, ids=None):
        plan = self.plan_organize(ids)
        moved, errors = 0, []
        old_dirs = set()
        for step in plan:
            try:
                os.makedirs(os.path.dirname(step["to"]), exist_ok=True)
                shutil.move(step["from"], step["to"])
                st = os.stat(step["to"])
                with self.lock:
                    self.db.execute("UPDATE books SET path=?, filename=?, mtime=? WHERE id=?",
                                    (step["to"], os.path.basename(step["to"]), st.st_mtime, step["id"]))
                old_dirs.add(os.path.dirname(step["from"]))
                moved += 1
            except Exception as e:
                errors.append(f"{step['title']}: {e}")
        with self.lock:
            self.db.commit()
        self._ensure_folder_watched(self.settings()["library_root"])
        # tidy away folders we emptied (only inside watched folders, never the roots)
        roots = {os.path.abspath(f) for f in self.settings()["folders"]}
        for d in sorted(old_dirs, key=len, reverse=True):
            while d and os.path.abspath(d) not in roots and any(d.startswith(r + "/") for r in roots):
                try:
                    os.rmdir(d)  # only succeeds when empty
                except OSError:
                    break
                d = os.path.dirname(d)
        return {"moved": moved, "errors": errors}

    # ---------------------------------------------------------------- trash
    def trash(self, book_id):
        """Move the file to the freedesktop trash (recoverable) and forget it."""
        b = self.get(book_id)
        if not b:
            return False
        if os.path.exists(b["path"]):
            ok = False
            if shutil.which("gio"):
                ok = subprocess.run(["gio", "trash", b["path"]], capture_output=True).returncode == 0
            if not ok:
                trash = os.path.expanduser("~/.local/share/Trash")
                os.makedirs(os.path.join(trash, "files"), exist_ok=True)
                os.makedirs(os.path.join(trash, "info"), exist_ok=True)
                dest = self._unique(os.path.join(trash, "files", b["filename"]))
                with open(os.path.join(trash, "info", os.path.basename(dest) + ".trashinfo"), "w") as f:
                    f.write(f"[Trash Info]\nPath={quote(b['path'])}\nDeletionDate={datetime.now():%Y-%m-%dT%H:%M:%S}\n")
                shutil.move(b["path"], dest)
        self.forget(book_id)
        return True

