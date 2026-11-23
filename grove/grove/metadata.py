"""Metadata + cover extraction for the formats Grove understands.

Everything here uses the Python standard library. If `pypdf` or `pypdfium2`
happen to be installed, PDFs get better metadata and real cover thumbnails.
"""
import base64
import io
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET

# extension -> (format label, category)
FORMATS = {
    "pdf": ("PDF", "Document"),
    "epub": ("EPUB", "Ebook"),
    "mobi": ("MOBI", "Ebook"),
    "azw": ("AZW", "Ebook"),
    "azw3": ("AZW3", "Ebook"),
    "kf8": ("KF8", "Ebook"),
    "prc": ("PRC", "Ebook"),
    "pdb": ("PDB", "Ebook"),
    "fb2": ("FB2", "Ebook"),
    "lit": ("LIT", "Ebook"),
    "djvu": ("DJVU", "Document"),
    "djv": ("DJVU", "Document"),
    "chm": ("CHM", "Document"),
    "cbz": ("CBZ", "Comic"),
    "cbr": ("CBR", "Comic"),
    "cb7": ("CB7", "Comic"),
    "txt": ("TXT", "Document"),
    "md": ("Markdown", "Document"),
    "rtf": ("RTF", "Document"),
    "doc": ("DOC", "Document"),
    "docx": ("DOCX", "Document"),
    "odt": ("ODT", "Document"),
    "ppt": ("PPT", "Document"),
    "pptx": ("PPTX", "Document"),
    "odp": ("ODP", "Document"),
}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

try:  # optional, nicer PDF metadata
    import pypdf  # type: ignore
except Exception:  # pragma: no cover
    pypdf = None

try:  # optional, real PDF cover renders
    import pypdfium2  # type: ignore
except Exception:  # pragma: no cover
    pypdfium2 = None

try:  # optional, cover thumbnails get resized (smaller = faster UI)
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None


def ext_of(path):
    name = os.path.basename(path).lower()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1]


def is_supported(path):
    name = os.path.basename(path)
    if name.startswith(".") or name.startswith("~$"):
        return False
    return ext_of(path) in FORMATS


# --------------------------------------------------------------------------
# filename heuristics
# --------------------------------------------------------------------------
_JUNK = re.compile(
    r"[\(\[](?:z-lib\.org|z-library|libgen[^\)\]]*|ebook|epub|pdf|mobi|retail|"
    r"www\.[^\)\]]+|[^\)\]]*\.(?:com|org|net))[\)\]]",
    re.I,
)


def guess_from_filename(path):
    stem = os.path.basename(path)
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = _JUNK.sub("", stem)
    stem = stem.replace("_", " ").strip()
    if stem.count(".") >= 2 and " " not in stem:
        stem = stem.replace(".", " ")
    stem = re.sub(r"\s+", " ", stem).strip(" -")
    title, author = stem, ""
    for sep in (" - ", " — ", " – ", " by "):
        if sep in stem:
            a, b = stem.split(sep, 1)
            if sep == " by ":
                title, author = a.strip(), b.strip()
            else:
                author, title = a.strip(), b.strip()
            break
    if author and (len(author) > 60 or re.search(r"\d{3,}", author)):
        title, author = stem, ""
    return title or os.path.basename(path), author


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean(s):
    if s is None:
        return ""
    if isinstance(s, bytes):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                s = s.decode(enc)
                break
            except Exception:
                continue
    s = str(s).replace("\x00", "")
    s = re.sub(r"<[^>]+>", " ", s)  # strip html in descriptions
    return re.sub(r"\s+", " ", s).strip()


def _year(s):
    m = re.search(r"(1[5-9]\d\d|20\d\d)", s or "")
    return m.group(1) if m else ""


def save_cover(data, dest_path, max_size=(360, 540)):
    """Write cover bytes to dest_path (jpeg). Returns True on success."""
    if not data:
        return False
    try:
        if Image is not None:
            img = Image.open(io.BytesIO(data))
            img = img.convert("RGB")
            img.thumbnail(max_size)
            img.save(dest_path, "JPEG", quality=82, optimize=True)
        else:
            with open(dest_path, "wb") as f:
                f.write(data)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# EPUB
# --------------------------------------------------------------------------
def epub_opf_path(z):
    try:
        root = ET.fromstring(z.read("META-INF/container.xml"))
        for el in root.iter():
            if _local(el.tag) == "rootfile" and el.get("full-path"):
                return el.get("full-path")
    except Exception:
        pass
    for n in z.namelist():
        if n.lower().endswith(".opf"):
            return n
    return None


def epub_package(path):
    """Return (opf_dir, manifest{id:(href,type,props)}, spine[ids], metadata_el)."""
    z = zipfile.ZipFile(path)
    opf = epub_opf_path(z)
    if not opf:
        raise ValueError("no OPF")
    root = ET.fromstring(z.read(opf))
    opf_dir = os.path.dirname(opf)
    manifest, spine, meta = {}, [], None
    for el in root:
        tag = _local(el.tag)
        if tag == "metadata":
            meta = el
        elif tag == "manifest":
            for it in el:
                if _local(it.tag) == "item":
                    manifest[it.get("id")] = (
                        it.get("href", ""),
                        it.get("media-type", ""),
                        it.get("properties", ""),
                    )
        elif tag == "spine":
            for it in el:
                if _local(it.tag) == "itemref" and it.get("linear", "yes") != "no":
                    spine.append(it.get("idref"))
    return z, opf_dir, manifest, spine, meta


def _join(base, href):
    from urllib.parse import unquote

    import posixpath

    href = unquote(href.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(base, href)) if base else posixpath.normpath(href)


def read_epub(path):
    info = {}
    cover = None
    z, opf_dir, manifest, _spine, meta = epub_package(path)
    with z:
        cover_id = None
        subjects = []
        if meta is not None:
            for el in meta:
                tag = _local(el.tag)
                text = _clean(el.text)
                if tag == "title" and text and "title" not in info:
                    info["title"] = text
                elif tag == "creator" and text:
                    info.setdefault("authors", []).append(text)
                elif tag == "language" and text:
                    info.setdefault("language", text)
                elif tag == "publisher" and text:
                    info.setdefault("publisher", text)
                elif tag == "date" and text:
                    info.setdefault("year", _year(text))
                elif tag == "description" and text:
                    info.setdefault("description", text)
                elif tag == "subject" and text:
                    subjects.append(text)
                elif tag == "meta":
                    if el.get("name") == "cover":
                        cover_id = el.get("content")
                    elif el.get("name") == "calibre:series" and el.get("content"):
                        info["series"] = el.get("content")
        if subjects:
            info["tags"] = subjects[:8]
        if info.get("authors"):
            info["author"] = " & ".join(dict.fromkeys(info.pop("authors")))
        # cover lookup
        href = None
        for _id, (h, mt, props) in manifest.items():
            if "cover-image" in (props or ""):
                href = h
                break
        if not href and cover_id and cover_id in manifest:
            h, mt, _ = manifest[cover_id]
            if mt.startswith("image"):
                href = h
        if not href:
            for _id, (h, mt, _) in manifest.items():
                if mt.startswith("image") and ("cover" in (_id or "").lower() or "cover" in h.lower()):
                    href = h
                    break
        names = set(z.namelist())
        if href:
            p = _join(opf_dir, href)
            if p in names:
                cover = z.read(p)
        if cover is None:
            imgs = [n for n in z.namelist() if n.lower().endswith(IMAGE_EXT)]
            if imgs:
                cover = z.read(imgs[0])
    return info, cover


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def read_pdf(path, want_cover=True):
    info, cover = {}, None
    if pypdf is not None:
        try:
            r = pypdf.PdfReader(path, strict=False)
            if r.is_encrypted:
                try:
                    r.decrypt("")
                except Exception:
                    pass
            m = r.metadata or {}
            t = _clean(m.get("/Title"))
            a = _clean(m.get("/Author"))
            if t and not re.match(r"^(untitled|microsoft word|document\d*)", t, re.I) and len(t) > 1:
                info["title"] = t
            if a and not re.match(r"^(user|admin|owner|unknown)$", a, re.I):
                info["author"] = a
            if m.get("/Subject"):
                info["description"] = _clean(m.get("/Subject"))
            kw = _clean(m.get("/Keywords"))
            if kw:
                info["tags"] = [k.strip() for k in re.split(r"[,;]", kw) if k.strip()][:8]
            info["year"] = _year(str(m.get("/CreationDate", "")))[:4]
            try:
                info["pages"] = len(r.pages)
            except Exception:
                pass
        except Exception:
            pass
    elif shutil.which("pdfinfo"):
        info = pdfinfo(path)
    else:
        try:
            with open(path, "rb") as f:
                head = f.read(256 * 1024)
            m = re.search(rb"/Title\s*\(([^)]{2,200})\)", head)
            if m:
                info["title"] = _clean(m.group(1))
            m = re.search(rb"/Author\s*\(([^)]{2,200})\)", head)
            if m:
                info["author"] = _clean(m.group(1))
            info["pages"] = len(re.findall(rb"/Type\s*/Page[^s]", head)) or None
        except Exception:
            pass
    if want_cover:
        cover = render_pdf_page(path)
    return info, cover


def render_pdf_page(path, height=540):
    """Screenshot the first page of a PDF as JPEG bytes.

    Tries, in order: pypdfium2 (pip), pdftoppm (poppler-utils, preinstalled on
    Ubuntu), mutool (mupdf-tools), ghostscript. Returns None if none work.
    """
    if pypdfium2 is not None:
        try:
            doc = pypdfium2.PdfDocument(path)
            try:
                page = doc[0]
                _w, h = page.get_size()
                pil = page.render(scale=height / max(h, 1)).to_pil()
                buf = io.BytesIO()
                pil.convert("RGB").save(buf, "JPEG", quality=82)
                return buf.getvalue()
            finally:
                doc.close()
        except Exception:
            pass
    with tempfile.TemporaryDirectory(prefix="grove-") as tmp:
        out = os.path.join(tmp, "page")
        attempts = []
        if shutil.which("pdftoppm"):
            attempts.append((["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-scale-to-y", str(height),
                              "-scale-to-x", "-1", "-png", path, out], out + ".png"))
        if shutil.which("mutool"):
            attempts.append((["mutool", "draw", "-q", "-h", str(height), "-o", out + ".png", path, "1"], out + ".png"))
        if shutil.which("gs"):
            attempts.append((["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dFirstPage=1", "-dLastPage=1",
                              "-sDEVICE=png16m", "-r60", "-o", out + ".png", path], out + ".png"))
        for cmd, result in attempts:
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40, check=True)
                if os.path.exists(result) and os.path.getsize(result) > 0:
                    with open(result, "rb") as f:
                        return f.read()
            except Exception:
                continue
    return None


def pdfinfo(path):
    """Title/author/pages via poppler's pdfinfo, used when pypdf isn't installed."""
    if not shutil.which("pdfinfo"):
        return {}
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
    except Exception:
        return {}
    info = {}
    for line in out.splitlines():
        key, _, val = line.partition(":")
        val = _clean(val)
        if not val:
            continue
        if key == "Title" and not re.match(r"^(untitled|microsoft word|document\d*)", val, re.I):
            info["title"] = val
        elif key == "Author" and not re.match(r"^(user|admin|owner|unknown)$", val, re.I):
            info["author"] = val
        elif key == "Pages" and val.isdigit():
            info["pages"] = int(val)
        elif key == "CreationDate":
            info["year"] = _year(val)
    return info


# --------------------------------------------------------------------------
# MOBI / AZW / AZW3 / PRC
# --------------------------------------------------------------------------
def read_mobi(path):
    info, cover = {}, None
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 86:
        return info, cover
    n_records = struct.unpack(">H", data[76:78])[0]
    offsets = [struct.unpack(">I", data[78 + i * 8: 82 + i * 8])[0] for i in range(n_records)]
    if not offsets:
        return info, cover

    def record(i):
        if i < 0 or i >= len(offsets):
            return b""
        end = offsets[i + 1] if i + 1 < len(offsets) else len(data)
        return data[offsets[i]:end]

    r0 = record(0)
    info["title"] = _clean(data[0:32].split(b"\x00")[0])
    if r0[16:20] != b"MOBI":
        return info, cover
    mobi_len = struct.unpack(">I", r0[20:24])[0]
    encoding = struct.unpack(">I", r0[28:32])[0]
    enc = "utf-8" if encoding == 65001 else "cp1252"
    name_off, name_len = struct.unpack(">II", r0[84:92])
    if name_len:
        info["title"] = _clean(r0[name_off:name_off + name_len].decode(enc, "replace"))
    first_img = struct.unpack(">I", r0[108:112])[0] if len(r0) >= 112 else 0xFFFFFFFF
    exth_flag = struct.unpack(">I", r0[128:132])[0] if len(r0) >= 132 else 0
    cover_off = None
    thumb_off = None
    if exth_flag & 0x40:
        p = 16 + mobi_len
        if r0[p:p + 4] == b"EXTH":
            count = struct.unpack(">I", r0[p + 8:p + 12])[0]
            q = p + 12
            authors, subjects = [], []
            for _ in range(count):
                if q + 8 > len(r0):
                    break
                typ, ln = struct.unpack(">II", r0[q:q + 8])
                val = r0[q + 8:q + ln]
                q += ln
                if typ == 100:
                    authors.append(_clean(val.decode(enc, "replace")))
                elif typ == 101:
                    info["publisher"] = _clean(val.decode(enc, "replace"))
                elif typ == 103:
                    info["description"] = _clean(val.decode(enc, "replace"))
                elif typ == 105:
                    subjects.append(_clean(val.decode(enc, "replace")))
                elif typ == 106:
                    info["year"] = _year(val.decode(enc, "replace"))
                elif typ == 503:
                    info["title"] = _clean(val.decode(enc, "replace"))
                elif typ == 524:
                    info["language"] = _clean(val.decode(enc, "replace"))
                elif typ == 201 and len(val) == 4:
                    cover_off = struct.unpack(">I", val)[0]
                elif typ == 202 and len(val) == 4:
                    thumb_off = struct.unpack(">I", val)[0]
            if authors:
                info["author"] = " & ".join(dict.fromkeys(a for a in authors if a))
            if subjects:
                info["tags"] = subjects[:8]
    if first_img != 0xFFFFFFFF:
        for off in (cover_off, thumb_off, 0):
            if off is None or off == 0xFFFFFFFF:
                continue
            rec = record(first_img + off)
            if rec[:3] == b"\xff\xd8\xff" or rec[:8] == b"\x89PNG\r\n\x1a\n" or rec[:3] == b"GIF":
                cover = rec
                break
    return info, cover


# --------------------------------------------------------------------------
# FB2
# --------------------------------------------------------------------------
def read_fb2(path):
    info, cover = {}, None
    root = ET.parse(path).getroot()
    cover_href = None
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "title-info":
            authors = []
            for sub in el:
                st = _local(sub.tag)
                if st == "book-title":
                    info["title"] = _clean(sub.text)
                elif st == "author":
                    parts = [(_clean(x.text)) for x in sub if _local(x.tag) in ("first-name", "middle-name", "last-name")]
                    name = " ".join(p for p in parts if p)
                    if name:
                        authors.append(name)
                elif st == "lang":
                    info["language"] = _clean(sub.text)
                elif st == "genre":
                    info.setdefault("tags", []).append(_clean(sub.text))
                elif st == "annotation":
                    info["description"] = _clean("".join(sub.itertext()))
                elif st == "date":
                    info["year"] = _year(_clean(sub.text) or sub.get("value", ""))
                elif st == "coverpage":
                    for img in sub.iter():
                        for k, v in img.attrib.items():
                            if k.endswith("href"):
                                cover_href = v.lstrip("#")
            if authors:
                info["author"] = " & ".join(authors)
        elif tag == "binary" and cover_href and el.get("id") == cover_href:
            try:
                cover = base64.b64decode(el.text or "")
            except Exception:
                pass
    return info, cover


# --------------------------------------------------------------------------
# CBZ / DOCX / ODT (zip based)
# --------------------------------------------------------------------------
def read_cbz(path):
    with zipfile.ZipFile(path) as z:
        imgs = sorted(n for n in z.namelist() if n.lower().endswith(IMAGE_EXT))
        return {"pages": len(imgs)}, (z.read(imgs[0]) if imgs else None)


def read_office(path):
    info = {}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        src = "docProps/core.xml" if "docProps/core.xml" in names else ("meta.xml" if "meta.xml" in names else None)
        if src:
            root = ET.fromstring(z.read(src))
            for el in root.iter():
                tag = _local(el.tag)
                t = _clean(el.text)
                if not t:
                    continue
                if tag == "title":
                    info["title"] = t
                elif tag in ("creator", "initial-creator") and "author" not in info:
                    info["author"] = t
                elif tag == "description":
                    info["description"] = t
                elif tag in ("keywords", "keyword"):
                    info.setdefault("tags", []).extend(k.strip() for k in re.split(r"[,;]", t) if k.strip())
        cover = None
        for n in ("Thumbnails/thumbnail.png", "docProps/thumbnail.jpeg"):
            if n in names:
                cover = z.read(n)
    return info, cover


READERS = {
    "epub": read_epub,
    "pdf": read_pdf,
    "mobi": read_mobi, "azw": read_mobi, "azw3": read_mobi, "kf8": read_mobi, "prc": read_mobi, "pdb": read_mobi,
    "fb2": read_fb2,
    "cbz": read_cbz,
    "docx": read_office, "odt": read_office, "pptx": read_office, "odp": read_office,
}


def extract(path):
    """Return (info dict, cover bytes or None). Never raises."""
    ext = ext_of(path)
    fmt, category = FORMATS.get(ext, (ext.upper(), "Document"))
    info, cover = {}, None
    reader = READERS.get(ext)
    if reader:
        try:
            info, cover = reader(path)
        except Exception:
            info, cover = {}, None
    g_title, g_author = guess_from_filename(path)
    if not info.get("title") or info["title"].lower() in ("unknown", "untitled"):
        info["title"] = g_title
    if not info.get("author") or info["author"].lower() in ("unknown", "unknown author"):
        info["author"] = g_author
    info["format"] = fmt
    info["category"] = category
    info.setdefault("tags", [])
    info["tags"] = [t for t in dict.fromkeys(t for t in info["tags"] if t and len(t) < 40)]
    return info, cover


def epub_toc_labels(path):
    """Map chapter file path -> human title, from the EPUB3 nav or EPUB2 NCX."""
    labels = {}
    try:
        z, opf_dir, manifest, _spine, _meta = epub_package(path)
    except Exception:
        return labels
    with z:
        for href, mtype, props in manifest.values():
            is_nav = "nav" in (props or "").split()
            is_ncx = mtype == "application/x-dtbncx+xml"
            if not (is_nav or is_ncx):
                continue
            doc_path = _join(opf_dir, href)
            base = os.path.dirname(doc_path)
            try:
                root = ET.fromstring(z.read(doc_path))
            except Exception:
                continue
            if is_ncx:
                for np in root.iter():
                    if _local(np.tag) != "navPoint":
                        continue
                    text = src = None
                    for ch in np:
                        if _local(ch.tag) == "navLabel":
                            text = _clean("".join(ch.itertext()))
                        elif _local(ch.tag) == "content":
                            src = ch.get("src")
                    if text and src:
                        labels.setdefault(_join(base, src), text)
            else:
                for a in root.iter():
                    if _local(a.tag) == "a" and a.get("href"):
                        text = _clean("".join(a.itertext()))
                        if text:
                            labels.setdefault(_join(base, a.get("href")), text)
            if labels:
                break
    return labels
