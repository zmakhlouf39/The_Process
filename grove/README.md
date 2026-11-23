# 🌿 Grove — your quiet library

A small, local, browser-based library for your ebooks and documents on Ubuntu/Linux.
Point it at a folder (default `~/Books`), and every file it recognises becomes part of an
interactive library. It doesn't care how the folder is organised — nested folders, messy
names, files from anywhere — it finds them, reads their metadata and covers, and keeps
track of them.

Light on resources: one Python process using only the standard library, with a plain HTML/JS page. No Electron, no database server, no build step.

## Run it

```bash
cd grove
./grove.sh            # opens http://localhost:8642 in your browser
```

Optional: add it to your app menu (and optionally start it at login):

```bash
./install.sh
```

Needs Python 3.8+ (already on Ubuntu). Optional extras for nicer results:

```bash
pip install --user pypdf pypdfium2 pillow   # PDF titles/page counts, real PDF cover thumbnails, smaller covers
```

## What it does

- **Recognises** PDF, EPUB, MOBI, AZW, AZW3, KF8, PRC, FB2, DjVu, CHM, CBZ/CBR/CB7, TXT, Markdown, RTF, DOC/DOCX, ODT, PPT/PPTX/ODP.
- **Reads metadata and covers** from EPUB, MOBI/AZW3, FB2, PDF, CBZ and DOCX/ODT. If a file has no metadata, it guesses from the filename (`Author - Title.pdf`). If a book has no cover, it gets a generated forest-coloured one.
- **Watches folders.** Drop files into `~/Books` (or any folder you add in Settings) and they show up within a minute. Click ↻ to check right away.
- **Follows your files.** Move or rename a file and Grove recognises it by content, so ratings, notes and progress stay attached.
- **Sort and filter** by title, author, recently added or opened, rating, size, format or year. Shelves cover *Continue reading*, *Favorites*, *To read*, *Finished*, *Ebooks / Documents / Comics*, *Duplicates* and *Missing files*. Filter by format, tag or author, and search across titles, authors, tags and notes (press `/`).
- **Manage:** edit title, author, series, year and tags, rate books from 1 to 5 stars, add notes, set a reading status, and mark favorites. Use **Select** to tag or update many books at once.
- **Read in the browser:** EPUB (built-in reader that remembers your chapter and scroll position, with font size and night mode), PDF, TXT and MD. Everything else opens in your default app with **Open in app**.
- **Tidy up** (optional): moves files into neat folders such as `Books/Ebooks/Frank Herbert/Dune.azw3`. It always shows you the plan first. Pick the layout in Settings.
- **Add files** by dragging them onto the window or clicking **＋ Add**. They land in `~/Books/Inbox`.
- **Duplicates** are flagged. **Move to trash** uses your normal system Trash, so it can be undone.
- Day 🌿 / night 🌙 theme. It also works on a phone: run `./grove.sh --host 0.0.0.0` and open `http://<laptop-ip>:8642` on the same wifi.

## Where things live

- Your files stay where they are. Grove only moves them when you use **Tidy up**.
- Library index and covers: `~/.local/share/grove/`. Delete this folder to start fresh. Your books are not touched.
