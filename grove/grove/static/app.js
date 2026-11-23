/* Grove — front end. Plain JS, no build step, no frameworks. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("grove." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("grove." + k, JSON.stringify(v)); } catch {} },
};

const api = async (path, opts = {}) => {
  const r = await fetch(path, {
    ...opts,
    headers: opts.body && !(opts.body instanceof Blob) ? { "Content-Type": "application/json", ...(opts.headers || {}) } : opts.headers,
    body: opts.body && !(opts.body instanceof Blob) && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};

const S = {
  books: [],
  byId: new Map(),
  shelf: store.get("shelf", "all"),
  format: null, tag: null, author: null,
  q: "",
  sort: store.get("sort", "title"),
  view: store.get("view", "grid"),
  selectMode: false,
  selected: new Set(),
  lastScan: null,
  openId: null,
};

const READABLE = new Set(["epub", "pdf", "txt", "md"]);

const SHELVES = [
  { id: "all", label: "All books", ico: "🌳", f: () => true },
  { id: "reading", label: "Continue reading", ico: "🔖", f: (b) => b.status === "reading" },
  { id: "favorites", label: "Favorites", ico: "♥", f: (b) => b.favorite },
  { id: "unread", label: "To read", ico: "🌱", f: (b) => b.status === "unread" },
  { id: "finished", label: "Finished", ico: "🍂", f: (b) => b.status === "finished" },
  { id: "recent", label: "Recently added", ico: "✨", f: (b) => Date.now() / 1000 - b.added_at < 14 * 86400 },
  { id: "Ebook", label: "Ebooks", ico: "📖", f: (b) => b.category === "Ebook" },
  { id: "Document", label: "Documents", ico: "📄", f: (b) => b.category === "Document" },
  { id: "Comic", label: "Comics", ico: "🎨", f: (b) => b.category === "Comic", hideEmpty: true },
  { id: "dupes", label: "Duplicates", ico: "👯", f: (b) => b.duplicate, hideEmpty: true },
  { id: "missing", label: "Missing files", ico: "🍃", f: (b) => b.missing, hideEmpty: true },
];

/* ------------------------------------------------------------------ utils */
const fmtSize = (n) => n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1e3)) + " KB";
const fmtDate = (t) => t ? new Date(t * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—";
const hash = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) | 0; return Math.abs(h); };
const PALETTE = [
  ["#5f8a55", "#2f4a35"], ["#7c9a6a", "#3e5a44"], ["#8a6f52", "#4f3d2c"], ["#6d8f8a", "#2f4b49"],
  ["#a3b86c", "#55703a"], ["#9a7a5f", "#5d4632"], ["#5e7d6b", "#263b31"], ["#b0925a", "#6b5530"],
  ["#7a8f5a", "#3a4a2a"], ["#6b8c7a", "#34503f"],
];
const LEAF = '<svg viewBox="0 0 32 32"><path d="M27 5C14 5 6 11 6 20c0 2 .5 4 1.5 5.5C9 21 13 16 19 13c-5 4-9 9-10.5 14C10 28 12 28.5 14 28.5 23 28.5 27 19 27 5z" fill="currentColor"/></svg>';

function coverHTML(b) {
  const [c1, c2] = PALETTE[hash(b.title + b.author) % PALETTE.length];
  const gen = `<div class="gen-cover" style="background:linear-gradient(160deg,${c1},${c2})">
      <div class="gt">${esc(b.title)}</div><div class="ga">${esc(b.author)}</div>${LEAF}</div>`;
  const img = b.cover_url ? `<img loading="lazy" src="${b.cover_url}" alt="" onerror="this.remove()">` : "";
  return gen + img;
}

function progressPct(b) {
  if (b.status === "finished") return 100;
  const p = b.progress;
  if (p && p.total) return Math.round(((p.chapter + (p.scroll || 0)) / p.total) * 100);
  return 0;
}

function toast(msg, ms = 2600) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

/* ------------------------------------------------------------------ data */
async function load() {
  const data = await api("/api/books");
  S.books = data.books;
  S.byId = new Map(S.books.map((b) => [b.id, b]));
  S.lastScan = data.scan.last;
  renderSidebar();
  renderBooks();
  updateScan(data.scan);
  if (S.openId && S.byId.has(S.openId) && $("#drawer").classList.contains("on")) renderDrawer(S.byId.get(S.openId));
}

function filtered() {
  const shelf = SHELVES.find((s) => s.id === S.shelf) || SHELVES[0];
  const words = S.q.toLowerCase().split(/\s+/).filter(Boolean);
  let list = S.books.filter((b) => {
    if (S.shelf !== "missing" && b.missing && S.shelf !== "all") return false;
    if (!shelf.f(b)) return false;
    if (S.format && b.format !== S.format) return false;
    if (S.tag && !b.tags.includes(S.tag)) return false;
    if (S.author && b.author !== S.author) return false;
    if (words.length) {
      const hay = [b.title, b.author, b.series, b.tags.join(" "), b.notes, b.filename, b.publisher, b.format].join(" ").toLowerCase();
      return words.every((w) => hay.includes(w));
    }
    return true;
  });
  const by = {
    title: (a, b) => a.title.localeCompare(b.title, undefined, { numeric: true, sensitivity: "base" }),
    author: (a, b) => (a.author || "~").localeCompare(b.author || "~", undefined, { sensitivity: "base" }) || by.title(a, b),
    added: (a, b) => b.added_at - a.added_at,
    opened: (a, b) => (b.last_opened || 0) - (a.last_opened || 0),
    rating: (a, b) => b.rating - a.rating || by.title(a, b),
    size: (a, b) => b.size - a.size,
    format: (a, b) => a.format.localeCompare(b.format) || by.title(a, b),
    year: (a, b) => (b.year || "0").localeCompare(a.year || "0") || by.title(a, b),
  };
  const sort = S.shelf === "reading" && S.sort === "title" ? "opened" : S.sort;
  return list.sort(by[sort] || by.title);
}

/* ------------------------------------------------------------------ sidebar */
function renderSidebar() {
  const live = S.books.filter((b) => !b.missing);
  $("#shelves").innerHTML = SHELVES.map((s) => {
    const n = (s.id === "missing" ? S.books : live).filter(s.f).length;
    if (s.hideEmpty && !n) return "";
    return `<a data-shelf="${s.id}" class="${S.shelf === s.id ? "on" : ""}"><span class="ico">${s.ico}</span>${s.label}<span class="n">${n}</span></a>`;
  }).join("");

  const count = (key) => {
    const m = new Map();
    for (const b of live) for (const v of [].concat(b[key] || [])) if (v) m.set(v, (m.get(v) || 0) + 1);
    return [...m].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(b[0]));
  };
  $("#formats").innerHTML = count("format").map(([f, n]) =>
    `<span class="chip ${S.format === f ? "on" : ""}" data-format="${esc(f)}">${esc(f)}<small>${n}</small></span>`).join("") || '<span class="hint">—</span>';
  const tags = count("tags");
  $("#tags").innerHTML = tags.slice(0, 40).map(([t, n]) =>
    `<span class="chip ${S.tag === t ? "on" : ""}" data-tag="${esc(t)}">${esc(t)}<small>${n}</small></span>`).join("") || '<span class="hint" style="padding:0 6px">Add tags to books to grow shelves here.</span>';
  $("#authors").innerHTML = count("author").slice(0, 300).map(([a, n]) =>
    `<a data-author="${esc(a)}" class="${S.author === a ? "on" : ""}">${esc(a)}<span>${n}</span></a>`).join("");
  const total = live.reduce((s, b) => s + b.size, 0);
  const done = live.filter((b) => b.status === "finished").length;
  $("#stats").innerHTML = `${live.length} files · ${fmtSize(total)}<br>${done} finished 🍂`;
}

/* ------------------------------------------------------------------ books */
function renderBooks() {
  const list = filtered();
  const grid = $("#books");
  grid.className = "grid" + (S.view === "list" ? " list" : "");
  const shelf = SHELVES.find((s) => s.id === S.shelf) || SHELVES[0];
  let title = shelf.label;
  if (S.author) title = S.author;
  if (S.tag) title = "#" + S.tag;
  if (S.format) title += ` · ${S.format}`;
  $("#viewTitle").textContent = title;
  $("#viewCount").textContent = `${list.length} ${list.length === 1 ? "item" : "items"}`;
  $$("#viewSeg button").forEach((b) => b.classList.toggle("on", b.dataset.view === S.view));
  $("#sort").value = S.sort;

  grid.innerHTML = list.map((b) => {
    const pct = progressPct(b);
    return `<article class="card ${S.selected.has(b.id) ? "sel" : ""} ${b.missing ? "missing" : ""}" data-id="${b.id}" title="${esc(b.title)}">
      ${S.selectMode ? '<div class="tick">✓</div>' : ""}
      <div class="cover">${coverHTML(b)}
        <span class="badge">${esc(b.format)}</span>
        ${b.favorite ? '<span class="heart">♥</span>' : ""}
        ${pct && pct < 100 ? `<div class="progress-bar"><i style="width:${pct}%"></i></div>` : ""}
      </div>
      <div class="meta">
        <div class="t"><span class="status-dot s-${b.status}"></span>${esc(b.title)}</div>
        <div class="a">${esc(b.author || "Unknown author")}</div>
        ${b.missing ? '<div class="warn">file not found</div>' : ""}
      </div>
      <div class="extra">
        ${b.rating ? `<span>${"★".repeat(b.rating)}</span>` : ""}
        ${pct && pct < 100 ? `<span>${pct}%</span>` : ""}
        <span class="fmt">${esc(b.format)}</span><span>${fmtSize(b.size)}</span>
      </div>
    </article>`;
  }).join("");

  const empty = $("#empty");
  if (list.length) { empty.classList.add("hidden"); return; }
  empty.classList.remove("hidden");
  if (!S.books.length) {
    api("/api/settings").then((s) => {
      empty.innerHTML = `<div class="big">🌱</div><h3>Your grove is empty — for now</h3>
        <p>Drop books (PDF, EPUB, MOBI, AZW3, FB2, DjVu, CBZ, DOCX…) into <code>${esc(s.folders[0] || "~/Books")}</code>,<br>
        drag them onto this window, or press <b>＋ Add</b>. Any subfolders work — Grove finds them.</p>`;
    });
  } else {
    empty.innerHTML = `<div class="big">🍃</div><h3>Nothing here</h3><p>No books match this view.</p>`;
  }
}

/* ------------------------------------------------------------------ drawer */
function openDrawer(id) {
  const b = S.byId.get(id);
  if (!b) return;
  S.openId = id;
  renderDrawer(b);
  $("#drawer").classList.add("on");
  $("#scrim").classList.add("on");
}
function closeDrawer() {
  $("#drawer").classList.remove("on");
  $("#scrim").classList.remove("on");
  $("#sidebar").classList.remove("on");
  S.openId = null;
}

function renderDrawer(b) {
  const readable = READABLE.has(b.ext) && !b.missing;
  $("#drawerBody").innerHTML = `
    <div class="d-head">
      <div class="cover">${coverHTML(b)}</div>
      <div style="min-width:0">
        <h2>${esc(b.title)}</h2>
        <div class="muted">${esc(b.author || "Unknown author")}</div>
        <div style="margin-top:8px"><span class="fmt">${esc(b.format)}</span> <span class="muted" style="font-size:13px">${fmtSize(b.size)}${b.pages ? ` · ${b.pages} pages` : ""}</span></div>
        <div class="stars" id="stars" style="margin-top:8px">${[1, 2, 3, 4, 5].map((i) => `<span data-r="${i}" class="${i <= b.rating ? "on" : ""}">★</span>`).join("")}</div>
      </div>
    </div>
    <div class="d-actions">
      ${readable ? '<button class="btn" data-act="read">📖 Read</button>' : ""}
      ${!b.missing ? '<button class="btn ghost" data-act="open">Open in app</button>' : ""}
      ${!b.missing ? '<button class="btn ghost" data-act="reveal">Show folder</button>' : ""}
      ${!b.missing ? `<a class="btn ghost" style="display:inline-grid;place-items:center;text-decoration:none" href="${b.file_url}?download=1">Download</a>` : ""}
      <button class="btn ghost" data-act="fav">${b.favorite ? "♥ Favorite" : "♡ Favorite"}</button>
    </div>
    <div class="field"><label>Status</label>
      <div class="status-seg" id="statusSeg">${["unread", "reading", "finished"].map((s) =>
        `<button data-s="${s}" class="${b.status === s ? "on" : ""}">${{ unread: "🌱 To read", reading: "🔖 Reading", finished: "🍂 Finished" }[s]}</button>`).join("")}</div>
    </div>
    <div class="field"><label>Title</label><input data-f="title" value="${esc(b.title)}"></div>
    <div class="field"><label>Author</label><input data-f="author" value="${esc(b.author)}" list="authorList"></div>
    <datalist id="authorList">${[...new Set(S.books.map((x) => x.author).filter(Boolean))].slice(0, 500).map((a) => `<option value="${esc(a)}">`).join("")}</datalist>
    <div class="row2">
      <div class="field"><label>Series</label><input data-f="series" value="${esc(b.series)}"></div>
      <div class="field"><label>Year</label><input data-f="year" value="${esc(b.year)}"></div>
    </div>
    <div class="field"><label>Tags (comma separated)</label><input data-f="tags" value="${esc(b.tags.join(", "))}" placeholder="fantasy, uni, to-print…"></div>
    <div class="field"><label>Notes</label><textarea data-f="notes" placeholder="Thoughts, quotes, where you left off…">${esc(b.notes)}</textarea></div>
    ${b.description ? `<div class="field"><label>Description</label><div class="desc">${esc(b.description)}</div></div>` : ""}
    <div class="file-info">
      <b>File</b> ${esc(b.path)}<br>
      <b>Added</b> ${fmtDate(b.added_at)} · <b>Last opened</b> ${fmtDate(b.last_opened)}
      ${b.publisher ? `<br><b>Publisher</b> ${esc(b.publisher)}` : ""}${b.language ? ` · <b>Language</b> ${esc(b.language)}` : ""}
      ${b.duplicate ? '<br><b style="color:var(--berry)">Looks like a duplicate</b> — the same file exists elsewhere in your library.' : ""}
      ${b.missing ? '<br><b style="color:var(--berry)">File not found.</b> If it was on a USB drive, plug it in and rescan. If you moved it inside your folders, Grove will find it on the next scan.' : ""}
    </div>
    <div class="danger-zone">
      <button class="btn ghost sm" data-act="refresh" title="Re-read title/author/cover from the file">Re-read metadata</button>
      <button class="btn ghost sm" data-act="tidy">Tidy this file</button>
      <button class="btn ghost sm" data-act="forget" title="Remove from Grove, keep the file">Forget</button>
      ${!b.missing ? '<button class="btn danger sm" data-act="trash">Move to trash</button>' : ""}
    </div>`;
}

async function saveField(id, fields) {
  const b = await api(`/api/books/${id}/update`, { method: "POST", body: fields });
  Object.assign(S.byId.get(id), b);
  renderSidebar();
  renderBooks();
  return b;
}

let saveTimer;
$("#drawerBody").addEventListener("input", (e) => {
  const f = e.target.dataset.f;
  if (!f || !S.openId) return;
  const id = S.openId;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveField(id, { [f]: e.target.value }), 450);
});

$("#drawerBody").addEventListener("click", async (e) => {
  const b = S.byId.get(S.openId);
  if (!b) return;
  const star = e.target.closest("[data-r]");
  if (star) {
    const r = +star.dataset.r === b.rating ? 0 : +star.dataset.r;
    renderDrawer(await saveField(b.id, { rating: r }));
    return;
  }
  const st = e.target.closest("[data-s]");
  if (st) { renderDrawer(await saveField(b.id, { status: st.dataset.s })); return; }
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!act) return;
  if (act === "read") openReader(b);
  if (act === "open") { await api(`/api/books/${b.id}/open`, { method: "POST" }); toast("Opening in your default app…"); }
  if (act === "reveal") { await api(`/api/books/${b.id}/reveal`, { method: "POST" }); toast("Opening folder…"); }
  if (act === "fav") renderDrawer(await saveField(b.id, { favorite: b.favorite ? 0 : 1 }));
  if (act === "refresh") { const nb = await api(`/api/books/${b.id}/refresh`, { method: "POST" }); await load(); renderDrawer(S.byId.get(nb.id)); toast("Metadata refreshed"); }
  if (act === "tidy") openTidy([b.id]);
  if (act === "forget" && confirm(`Remove “${b.title}” from Grove?\nThe file stays on disk (if it's in a watched folder it will come back on the next scan).`)) {
    await api(`/api/books/${b.id}/forget`, { method: "POST" }); closeDrawer(); load();
  }
  if (act === "trash" && confirm(`Move “${b.title}” to the trash?\nYou can restore it from your system Trash.`)) {
    await api(`/api/books/${b.id}/trash`, { method: "POST" }); closeDrawer(); toast("Moved to trash 🍂"); load();
  }
});

/* ------------------------------------------------------------------ reader */
const R = { book: null, chapters: [], i: 0, font: store.get("font", 19), css: "", saveT: null };

async function openReader(b) {
  R.book = b;
  $("#reader").classList.remove("hidden");
  $("#readerTitle").textContent = b.title;
  const stage = $("#readerStage"), tools = $("#readerTools");
  stage.innerHTML = "";
  tools.innerHTML = `<button class="btn ghost sm" id="rDone">🍂 Finished</button>`;
  $("#rDone").onclick = async () => { await saveField(b.id, { status: "finished" }); toast("Marked as finished — nice 🍂"); };
  if (b.ext === "pdf") {
    stage.innerHTML = `<iframe src="${b.file_url}"></iframe>`;
    saveField(b.id, { status: b.status === "unread" ? "reading" : b.status });
  } else if (b.ext === "txt" || b.ext === "md") {
    const { text } = await api(`/api/books/${b.id}/text`);
    const pre = document.createElement("pre");
    pre.textContent = text;
    stage.append(pre);
  } else if (b.ext === "epub") {
    const data = await api(`/api/books/${b.id}/epub`);
    R.chapters = data.chapters; R.css = data.css;
    const p = b.progress || {};
    R.i = Math.min(p.chapter || 0, R.chapters.length - 1);
    tools.innerHTML = `
      <button class="icon-btn" id="rPrev" title="Previous (←)">‹</button>
      <select id="rCh" class="sm">${R.chapters.map((c, i) => `<option value="${i}">${esc(data.labels[i] || `${i + 1}. ` + c.split("/").pop().replace(/\.x?html?$/i, "").replace(/[_-]/g, " "))}</option>`).join("")}</select>
      <button class="icon-btn" id="rNext" title="Next (→)">›</button>
      <button class="icon-btn" id="rSmall" title="Smaller text">A−</button>
      <button class="icon-btn" id="rBig" title="Bigger text">A+</button>` + tools.innerHTML;
    $("#rDone").onclick = async () => { await saveField(b.id, { status: "finished" }); toast("Marked as finished — nice 🍂"); };
    $("#rPrev").onclick = () => chapter(R.i - 1);
    $("#rNext").onclick = () => chapter(R.i + 1);
    $("#rCh").onchange = (e) => chapter(+e.target.value);
    $("#rSmall").onclick = () => { R.font = Math.max(13, R.font - 1); store.set("font", R.font); styleFrame(); };
    $("#rBig").onclick = () => { R.font = Math.min(30, R.font + 1); store.set("font", R.font); styleFrame(); };
    chapter(R.i, p.scroll || 0);
  } else {
    stage.innerHTML = `<div class="empty"><div class="big">📚</div><h3>Open this one in an app</h3><p>${esc(b.format)} files can't be shown in the browser.</p></div>`;
  }
}

function frameDoc() { const f = $("#readerStage iframe"); try { return f && f.contentDocument; } catch { return null; } }

function styleFrame() {
  const d = frameDoc();
  if (!d || !d.documentElement) return;
  let st = d.getElementById("grove-style");
  if (!st) {
    st = d.createElementNS("http://www.w3.org/1999/xhtml", "style");
    st.id = "grove-style";
    (d.head || d.documentElement).appendChild(st);
  }
  const dark = document.documentElement.dataset.theme === "dark";
  st.textContent = R.css + `body{font-size:${R.font}px!important}` +
    (dark ? "html{background:#24332a!important}body,body *{color:#dfe9d6!important;background-color:transparent!important}a{color:#93bb80!important}" : "html{background:#fbfcf6!important}");
}

function chapter(i, scroll = 0) {
  if (i < 0 || i >= R.chapters.length) return;
  R.i = i;
  $("#rCh").value = i;
  const f = document.createElement("iframe");
  f.setAttribute("sandbox", "allow-same-origin"); // book content can't run scripts
  f.src = `/api/books/${R.book.id}/epub/${R.chapters[i].split("/").map(encodeURIComponent).join("/")}`;
  f.onload = () => {
    styleFrame();
    const w = f.contentWindow, d = f.contentDocument;
    const el = d.scrollingElement || d.documentElement;
    if (scroll) setTimeout(() => { el.scrollTop = scroll * (el.scrollHeight - el.clientHeight); }, 30);
    w.addEventListener("scroll", () => {
      clearTimeout(R.saveT);
      R.saveT = setTimeout(() => saveProgress(el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight)), 800);
    });
    d.addEventListener("keydown", readerKeys);
    // links between chapters
    d.addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest("a[href]");
      if (!a) return;
      const url = new URL(a.href, f.src);
      if (url.origin !== location.origin) { e.preventDefault(); window.open(url.href, "_blank", "noopener"); return; }
      const inner = decodeURIComponent(url.pathname.split("/epub/")[1] || "");
      const idx = R.chapters.indexOf(inner);
      if (idx >= 0 && idx !== R.i) { e.preventDefault(); chapter(idx); }
    });
  };
  $("#readerStage").replaceChildren(f);
  saveProgress(scroll);
}

function saveProgress(scroll) {
  const b = R.book;
  if (!b) return;
  const progress = { chapter: R.i, scroll: Math.min(1, Math.max(0, scroll || 0)), total: R.chapters.length };
  b.progress = progress;
  api(`/api/books/${b.id}/update`, { method: "POST", body: { progress, ...(b.status === "unread" ? { status: "reading" } : {}) } })
    .then((nb) => Object.assign(b, nb));
}

function closeReader() {
  $("#reader").classList.add("hidden");
  $("#readerStage").innerHTML = "";
  R.book = null;
  load();
}

function readerKeys(e) {
  if ($("#reader").classList.contains("hidden")) return;
  if (e.key === "Escape") closeReader();
  if (!R.chapters.length || !R.book || R.book.ext !== "epub") return;
  if (e.key === "ArrowRight") chapter(R.i + 1);
  if (e.key === "ArrowLeft") chapter(R.i - 1);
}
$("#readerClose").onclick = closeReader;

/* ------------------------------------------------------------------ modals */
function modal(html) {
  $("#modalCard").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.closest("[data-close=modal]")) closeModal(); });

const PATTERNS = {
  "{category}/{author}/{title}": "Type › Author › Title",
  "{author}/{title}": "Author › Title",
  "{letter}/{author}/{title}": "A–Z › Author › Title",
  "{format}/{author}/{title}": "Format › Author › Title",
  "{category}/{author}/{series}/{title}": "Type › Author › Series › Title",
};

async function openSettings() {
  const s = await api("/api/settings");
  const custom = !PATTERNS[s.organize_pattern];
  modal(`
    <h2>Settings</h2>
    <p class="hint">Grove watches these folders (and everything inside them). It doesn't care how they're organised — any file it recognises becomes part of your library.</p>
    <div class="field"><label>Watched folders</label>
      <div id="folderRows">${s.folders.map((f) => folderRow(f)).join("")}</div>
      <button class="btn ghost sm" id="addFolder">＋ Add folder</button>
    </div>
    <div class="field"><label>Main library folder (where uploads & tidied files go)</label><input id="root" value="${esc(s.library_root)}"></div>
    <div class="field"><label>Tidy-up layout</label>
      <select id="pattern">${Object.entries(PATTERNS).map(([k, v]) => `<option value="${k}" ${k === s.organize_pattern ? "selected" : ""}>${v}</option>`).join("")}
        <option value="custom" ${custom ? "selected" : ""}>Custom…</option></select>
      <input id="patternCustom" class="${custom ? "" : "hidden"}" style="margin-top:6px" value="${esc(s.organize_pattern)}">
      <div class="hint" style="margin-top:4px">Placeholders: {category} {author} {title} {format} {series} {year} {letter} {language}</div>
    </div>
    <div class="field"><label>Check folders for new files every (seconds)</label><input id="interval" type="number" min="10" value="${s.scan_interval}"></div>
    <div class="foot"><button class="btn ghost" data-close="modal">Cancel</button><button class="btn" id="saveSettings">Save</button></div>`);
  $("#addFolder").onclick = () => $("#folderRows").insertAdjacentHTML("beforeend", folderRow(""));
  $("#folderRows").onclick = (e) => { if (e.target.dataset.rm !== undefined) e.target.parentElement.remove(); };
  $("#pattern").onchange = (e) => $("#patternCustom").classList.toggle("hidden", e.target.value !== "custom");
  $("#saveSettings").onclick = async () => {
    const pat = $("#pattern").value === "custom" ? $("#patternCustom").value : $("#pattern").value;
    await api("/api/settings", { method: "POST", body: {
      folders: $$("#folderRows input").map((i) => i.value).filter(Boolean),
      library_root: $("#root").value, organize_pattern: pat, scan_interval: +$("#interval").value || 60,
    } });
    closeModal(); toast("Saved — rescanning 🌿"); pollScan();
  };
}
const folderRow = (f) => `<div class="folder-row"><input value="${esc(f)}" placeholder="/home/you/Documents/Uni"><button class="icon-btn" data-rm title="Remove">✕</button></div>`;

const rel = (p, root) => p.startsWith(root + "/") ? p.slice(root.length + 1) : p;
async function openTidy(ids) {
  const q = ids && ids.length ? `?ids=${ids.join(",")}` : "";
  const { plan, root } = await api("/api/organize/preview" + q);
  if (!plan.length) { toast("Everything is already tidy 🌿"); return; }
  modal(`
    <h2>Tidy up</h2>
    <p class="hint">Grove will move ${plan.length} file${plan.length > 1 ? "s" : ""} into neat folders under <b>${esc(root)}</b>.
      Your ratings, notes and progress come along. You can change the layout in Settings.</p>
    <div class="plan">${plan.slice(0, 400).map((p) => `<div><div class="from">${esc(rel(p.from, root))}</div><div class="to">→ ${esc(rel(p.to, root))}</div></div>`).join("")}
      ${plan.length > 400 ? `<div class="hint">…and ${plan.length - 400} more</div>` : ""}</div>
    <div class="foot"><button class="btn ghost" data-close="modal">Not now</button><button class="btn" id="doTidy">Move ${plan.length} file${plan.length > 1 ? "s" : ""}</button></div>`);
  $("#doTidy").onclick = async () => {
    $("#doTidy").disabled = true; $("#doTidy").textContent = "Moving…";
    const r = await api("/api/organize", { method: "POST", body: { ids: ids || null } });
    closeModal();
    toast(`Tidied ${r.moved} file${r.moved === 1 ? "" : "s"} 🌿` + (r.errors.length ? ` · ${r.errors.length} couldn't move` : ""), 4000);
    if (r.errors.length) console.warn(r.errors);
    load();
  };
}

/* ------------------------------------------------------------------ uploads */
async function upload(files) {
  files = [...files];
  if (!files.length) return;
  let ok = 0;
  for (const [i, f] of files.entries()) {
    $("#scanStatus").textContent = `Planting ${i + 1}/${files.length}: ${f.name}`;
    try {
      await api("/api/upload", { method: "POST", body: f, headers: { "X-Filename": encodeURIComponent(f.name) } });
      ok++;
    } catch (e) { toast(`${f.name}: ${e.message}`, 4000); }
  }
  $("#scanStatus").textContent = "";
  if (ok) toast(`Added ${ok} file${ok > 1 ? "s" : ""} to your Inbox 🌱`);
  load();
}
$("#addBtn").onclick = () => $("#fileInput").click();
$("#fileInput").onchange = (e) => { upload(e.target.files); e.target.value = ""; };
let dragDepth = 0;
window.addEventListener("dragenter", (e) => { if (e.dataTransfer?.types.includes("Files")) { dragDepth++; $("#dropzone").classList.remove("hidden"); } });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#dropzone").classList.add("hidden"); } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $("#dropzone").classList.add("hidden"); upload(e.dataTransfer.files); });

/* ------------------------------------------------------------------ scanning */
function updateScan(st) {
  const icon = $("#scanIcon");
  icon.classList.toggle("spin", !!st.running);
  $("#scanStatus").textContent = st.running
    ? `Looking through your folders… ${st.done}/${st.found}${st.added ? ` · ${st.added} new` : ""}`
    : st.last ? `Last checked ${new Date(st.last * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "";
}
let polling = false;
async function pollScan(start = true) {
  if (start) updateScan(await api("/api/scan", { method: "POST" }));
  if (polling) return;
  polling = true;
  let st;
  do {
    await new Promise((r) => setTimeout(r, 900));
    st = await api("/api/scan");
    updateScan(st);
    if (st.added && st.done % 25 === 0) load();
  } while (st.running);
  polling = false;
  load();
}
// the server rescans by itself; quietly pick up changes
setInterval(async () => {
  if (!$("#reader").classList.contains("hidden")) return;
  try {
    const st = await api("/api/scan");
    if (st.running) pollScan(false);
    else if (st.last !== S.lastScan) load();
  } catch {}
}, 15000);

/* ------------------------------------------------------------------ selection */
function setSelectMode(on) {
  S.selectMode = on;
  S.selected.clear();
  $("#bulkBar").classList.toggle("hidden", !on);
  $("#selectBtn").classList.toggle("on", on);
  updateBulk();
  renderBooks();
}
function updateBulk() { $("#bulkCount").textContent = `${S.selected.size} selected`; }
$("#selectBtn").onclick = () => setSelectMode(!S.selectMode);
$("#bulkBar").addEventListener("click", async (e) => {
  const a = e.target.dataset.bulk;
  if (!a) return;
  const ids = [...S.selected];
  if (a === "done") return setSelectMode(false);
  if (a === "all") { filtered().forEach((b) => S.selected.add(b.id)); updateBulk(); return renderBooks(); }
  if (!ids.length) return toast("Select some books first");
  if (a === "tag") {
    const t = prompt("Tag to add to the selected books:");
    if (!t) return;
    await api("/api/bulk", { method: "POST", body: { ids, add_tags: t.split(",").map((x) => x.trim()).filter(Boolean) } });
  }
  if (a === "fav") await api("/api/bulk", { method: "POST", body: { ids, fields: { favorite: 1 } } });
  if (a === "tidy") return openTidy(ids);
  toast("Done 🌿");
  load();
});
$("#bulkStatus").onchange = async (e) => {
  if (!e.target.value || !S.selected.size) { e.target.value = ""; return; }
  await api("/api/bulk", { method: "POST", body: { ids: [...S.selected], fields: { status: e.target.value } } });
  e.target.value = "";
  toast("Done 🌿");
  load();
};

/* ------------------------------------------------------------------ events */
$("#books").addEventListener("click", (e) => {
  const card = e.target.closest(".card");
  if (!card) return;
  const id = +card.dataset.id;
  if (S.selectMode) {
    S.selected.has(id) ? S.selected.delete(id) : S.selected.add(id);
    card.classList.toggle("sel");
    updateBulk();
    return;
  }
  openDrawer(id);
});
$("#books").addEventListener("dblclick", (e) => {
  const card = e.target.closest(".card");
  const b = card && S.byId.get(+card.dataset.id);
  if (b && !S.selectMode && READABLE.has(b.ext) && !b.missing) { closeDrawer(); openReader(b); }
});

$(".sidebar").addEventListener("click", (e) => {
  const s = e.target.closest("[data-shelf]");
  const f = e.target.closest("[data-format]");
  const t = e.target.closest("[data-tag]");
  const a = e.target.closest("[data-author]");
  if (s) { S.shelf = s.dataset.shelf; S.format = S.tag = S.author = null; store.set("shelf", S.shelf); }
  else if (f) S.format = S.format === f.dataset.format ? null : f.dataset.format;
  else if (t) S.tag = S.tag === t.dataset.tag ? null : t.dataset.tag;
  else if (a) S.author = S.author === a.dataset.author ? null : a.dataset.author;
  else return;
  $("#sidebar").classList.remove("on");
  $("#scrim").classList.remove("on");
  renderSidebar();
  renderBooks();
  window.scrollTo({ top: 0 });
});

let qTimer;
$("#search").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { S.q = e.target.value; renderBooks(); }, 120); });
$("#sort").onchange = (e) => { S.sort = e.target.value; store.set("sort", S.sort); renderBooks(); };
$("#viewSeg").onclick = (e) => { const v = e.target.dataset.view; if (v) { S.view = v; store.set("view", v); renderBooks(); } };
$("#scanBtn").onclick = () => pollScan();
$("#tidyBtn").onclick = () => openTidy(null);
$("#settingsBtn").onclick = openSettings;
$("#menuBtn").onclick = () => { $("#sidebar").classList.add("on"); $("#scrim").classList.add("on"); };
$("#scrim").onclick = closeDrawer;
$("[data-close=drawer]").onclick = closeDrawer;

function setTheme(t) {
  document.documentElement.dataset.theme = t;
  $("#themeBtn").textContent = t === "dark" ? "☀" : "☾";
  store.set("theme", t);
  if (R.book) styleFrame();
}
$("#themeBtn").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");

document.addEventListener("keydown", (e) => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
  if (!$("#reader").classList.contains("hidden")) return readerKeys(e);
  if (e.key === "Escape") { if (!$("#modal").classList.contains("hidden")) closeModal(); else if (S.selectMode) setSelectMode(false); else closeDrawer(); }
  if (e.key === "/" && !typing) { e.preventDefault(); $("#search").focus(); }
});

setTheme(store.get("theme", "light"));
load().then(() => { if (!S.books.length) pollScan(); });
