/*
 * content.js — the in-page picker panel, injected into whatever page you're on
 * (an awarding authority's tender page) when you click the toolbar icon.
 *
 * It scans the page for links to documents, lets you tick the ones that belong
 * to the tender, shows the target act (auto-detected from an open app tab, or
 * type it), and hands the selection to the background worker to download +
 * upload. Re-clicking the icon toggles the panel.
 */

(() => {
  if (window.__tenderDocsPicker) { window.__tenderDocsPicker.toggle(); return; }

  const DOC_RE = /\.(pdf|docx?|xls[xmb]?|pptx?|csv|zip|rar|7z|odt|ods)(?=$|[?#])/i;
  const C = {
    ink: "#14181f", ink2: "#5b626d", rule: "#e6e8ec", paper: "#fff",
    accent: "#b41034", accent2: "#8a0c28", denim: "#003158", ok: "#1f8a63",
  };

  // ---- document discovery ------------------------------------------------ //
  function guessName(a, href) {
    try {
      const base = decodeURIComponent((new URL(href)).pathname.split("/").pop() || "");
      if (base && DOC_RE.test(base)) return base;
    } catch { /* ignore */ }
    const txt = (a.textContent || "").trim().replace(/\s+/g, " ");
    return txt ? txt.slice(0, 80) : "document";
  }

  function scan() {
    const seen = new Set();
    const out = [];
    for (const a of document.querySelectorAll("a[href]")) {
      const href = a.href;
      if (!href || seen.has(href)) continue;
      if (!/^https?:/i.test(href)) continue;
      const hit = DOC_RE.test(href) || DOC_RE.test(a.textContent || "");
      if (!hit) continue;
      seen.add(href);
      out.push({ url: href, name: guessName(a, href), text: (a.textContent || "").trim().replace(/\s+/g, " ").slice(0, 90) });
    }
    return out;
  }

  // ---- panel construction ------------------------------------------------ //
  const host = document.createElement("div");
  host.id = "__tender_docs_host";
  host.style.cssText = "all:initial; position:fixed; top:16px; right:16px; z-index:2147483647;";
  const root = host.attachShadow({ mode: "open" });
  document.documentElement.appendChild(host);

  const style = document.createElement("style");
  style.textContent = `
    * { box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
    .wrap { width:360px; max-height:82vh; display:flex; flex-direction:column;
      background:${C.paper}; color:${C.ink}; border:1px solid ${C.rule};
      border-radius:12px; box-shadow:0 8px 30px rgba(0,0,0,.18); overflow:hidden; font-size:13px; }
    .hd { display:flex; align-items:center; gap:8px; padding:10px 12px;
      background:linear-gradient(135deg,${C.accent},${C.denim}); color:#fff; }
    .hd b { font-size:13px; font-weight:600; letter-spacing:.01em; flex:1; }
    .hd button { all:unset; cursor:pointer; color:#fff; opacity:.85; font-size:16px; line-height:1; padding:2px 4px; }
    .hd button:hover { opacity:1; }
    .body { padding:10px 12px; overflow:auto; }
    .row { display:flex; gap:8px; align-items:flex-start; padding:6px 4px; border-bottom:1px solid ${C.rule}; }
    .row:last-child { border-bottom:0; }
    .row input[type=checkbox] { margin-top:2px; }
    .row .meta { min-width:0; flex:1; }
    .row .nm { font-weight:500; word-break:break-word; }
    .row .u { color:${C.ink2}; font-size:11px; word-break:break-all; }
    .empty { color:${C.ink2}; font-style:italic; padding:12px 4px; }
    .ft { border-top:1px solid ${C.rule}; padding:10px 12px; display:flex; flex-direction:column; gap:8px; background:#fbfcfd; }
    .fld { display:flex; flex-direction:column; gap:3px; }
    .fld label { font-size:11px; color:${C.ink2}; text-transform:uppercase; letter-spacing:.04em; }
    .fld input { padding:6px 8px; border:1px solid ${C.rule}; border-radius:7px; font-size:13px; color:${C.ink}; }
    .fld input:focus { outline:none; border-color:${C.accent}; box-shadow:0 0 0 3px #fbe9ec; }
    .bar { display:flex; gap:8px; align-items:center; }
    .bar .sp { flex:1; }
    .lnk { color:${C.accent}; cursor:pointer; text-decoration:none; font-size:11px; }
    .lnk:hover { text-decoration:underline; }
    button.go { all:unset; cursor:pointer; background:${C.accent}; color:#fff; padding:8px 16px;
      border-radius:8px; font-weight:600; font-size:12px; text-align:center; }
    button.go:hover { background:${C.accent2}; }
    button.go[disabled] { background:${C.ink2}; opacity:.5; cursor:default; }
    button.mini { all:unset; cursor:pointer; color:${C.accent}; font-size:11px; }
    button.mini:hover { text-decoration:underline; }
    .status { font-size:12px; padding:6px 0; }
    .status.err { color:${C.accent}; }
    .status.ok { color:${C.ok}; }
    .status a { color:${C.ok}; }
    .count { font-size:11px; color:${C.ink2}; }
  `;
  root.appendChild(style);

  const wrap = document.createElement("div");
  wrap.className = "wrap";
  wrap.innerHTML = `
    <div class="hd">
      <b>Tender docs → act</b>
      <button data-a="rescan" title="Re-scan the page">⟳</button>
      <button data-a="close" title="Close">✕</button>
    </div>
    <div class="body"><div class="empty">Scanning…</div></div>
    <div class="ft">
      <div class="fld">
        <label>Target act (ΑΔΑΜ)</label>
        <input type="text" data-a="adam" placeholder="open an act's edit page, or type it">
      </div>
      <div class="bar">
        <span class="count" data-a="count"></span>
        <span class="sp"></span>
        <button class="mini" data-a="all">select all</button>
        <button class="mini" data-a="none">none</button>
      </div>
      <div class="status" data-a="status"></div>
      <div class="bar">
        <a class="lnk" data-a="opts">Options</a>
        <span class="sp"></span>
        <button class="go" data-a="go">Upload</button>
      </div>
    </div>`;
  root.appendChild(wrap);

  const $ = (a) => root.querySelector(`[data-a="${a}"]`);
  const bodyEl = root.querySelector(".body");
  let docs = [];

  function render() {
    bodyEl.innerHTML = "";
    if (!docs.length) {
      bodyEl.innerHTML = `<div class="empty">No document links (PDF/Word/Excel/zip…) found on this page.</div>`;
    } else {
      for (let i = 0; i < docs.length; i++) {
        const d = docs[i];
        const row = document.createElement("label");
        row.className = "row";
        row.innerHTML = `
          <input type="checkbox" data-i="${i}" checked>
          <span class="meta">
            <span class="nm"></span>
            <span class="u"></span>
          </span>`;
        row.querySelector(".nm").textContent = d.name;
        row.querySelector(".u").textContent = d.url;
        bodyEl.appendChild(row);
      }
    }
    updateCount();
  }

  function checked() {
    return [...root.querySelectorAll('input[type=checkbox][data-i]')]
      .filter(c => c.checked).map(c => docs[+c.dataset.i]);
  }
  function updateCount() {
    const n = checked().length;
    $("count").textContent = docs.length ? `${n} / ${docs.length} selected` : "";
    $("go").disabled = !n;
  }

  function setStatus(text, cls) {
    const el = $("status");
    el.className = "status" + (cls ? " " + cls : "");
    el.textContent = "";
    if (text) el.append(text);
    return el;
  }

  function rescan() { docs = scan(); render(); }

  // ---- events ------------------------------------------------------------ //
  root.addEventListener("change", (e) => { if (e.target.matches("input[type=checkbox][data-i]")) updateCount(); });
  $("close").onclick = () => { host.style.display = "none"; };
  $("rescan").onclick = rescan;
  $("all").onclick = () => { root.querySelectorAll('input[data-i]').forEach(c => c.checked = true); updateCount(); };
  $("none").onclick = () => { root.querySelectorAll('input[data-i]').forEach(c => c.checked = false); updateCount(); };
  $("opts").onclick = () => { chrome.runtime.sendMessage({ type: "openOptions" }); };

  $("go").onclick = () => {
    const files = checked();
    const adam = $("adam").value.trim();
    if (!files.length) return;
    $("go").disabled = true;
    setStatus(`Downloading ${files.length} file(s) and uploading…`);
    chrome.runtime.sendMessage(
      { type: "upload", adam, files: files.map(f => ({ url: f.url, name: f.name })) },
      (res) => {
        $("go").disabled = false;
        if (chrome.runtime.lastError) { setStatus(chrome.runtime.lastError.message, "err"); return; }
        if (!res) { setStatus("No response from the extension.", "err"); return; }
        if (!res.ok) { setStatus(res.error || "Upload failed.", "err"); return; }
        const el = setStatus(`✓ Uploaded ${res.uploaded} file(s) to ${res.adam}. `, "ok");
        if (res.actUrl) {
          const a = document.createElement("a");
          a.href = res.actUrl; a.target = "_blank"; a.textContent = "open act";
          el.append(a);
        }
      });
  };

  const api = {
    toggle() { host.style.display = host.style.display === "none" ? "" : "none"; },
  };
  window.__tenderDocsPicker = api;

  // ---- boot -------------------------------------------------------------- //
  rescan();
  chrome.runtime.sendMessage({ type: "getContext" }, (ctx) => {
    if (chrome.runtime.lastError || !ctx) return;
    if (ctx.adam && !$("adam").value) $("adam").value = ctx.adam;
    if (!ctx.adam) setStatus("Tip: open the act's edit page in a tab and I'll target it automatically.", "");
  });
})();
