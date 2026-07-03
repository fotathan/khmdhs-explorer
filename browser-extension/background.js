/*
 * background.js — the extension's service worker.
 *
 * Three jobs:
 *  1. Toggle the in-page picker panel when the toolbar icon is clicked.
 *  2. Tell the panel which act to target — by finding an open app tab that's on
 *     an act's page and reading its ΑΔΑΜ out of the URL.
 *  3. Do the actual work: download the selected documents (cross-origin, which
 *     only the background context is allowed to do) and POST them as multipart
 *     `files` to the app's existing upload endpoint.
 *
 * No app changes are needed: it calls POST /admin/act/{adam}/attachments, which
 * already accepts multiple files and unpacks zips for search.
 */

const DEFAULTS = { baseUrl: "http://localhost:8000", authUser: "", authPass: "" };

async function getSettings() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  s.baseUrl = (s.baseUrl || DEFAULTS.baseUrl).trim().replace(/\/+$/, "");
  return s;
}

// Explicit Basic-Auth header, only if the user stored credentials in options.
// Otherwise we rely on the browser's cached session for the app origin (i.e.
// the auth you already did in the app tab).
function authHeader(s) {
  if (s.authUser && s.authPass) {
    const raw = `${s.authUser}:${s.authPass}`;
    return "Basic " + btoa(unescape(encodeURIComponent(raw)));
  }
  return null;
}

// Find the ΑΔΑΜ of an act that's open somewhere in the browser. Prefer a tab on
// the edit page; fall back to any /act/<adam> tab on the app host.
async function detectActAdam(baseUrl) {
  let host;
  try { host = new URL(baseUrl).host; } catch { return null; }
  const tabs = await chrome.tabs.query({});
  let fallback = null;
  for (const t of tabs) {
    if (!t.url) continue;
    let u;
    try { u = new URL(t.url); } catch { continue; }
    if (u.host !== host) continue;
    const m = u.pathname.match(/\/act\/([^/]+)/);
    if (!m) continue;
    const adam = decodeURIComponent(m[1]);
    if (/\/edit(\/|$)/.test(u.pathname)) return adam;   // strongest signal
    if (!fallback) fallback = adam;
  }
  return fallback;
}

// Best filename for a downloaded document: the server's Content-Disposition if
// it gives one (handles Greek via RFC 5987), else the URL's last path segment.
function filenameFor(resp, url) {
  const cd = resp.headers.get("content-disposition") || "";
  let m = cd.match(/filename\*=UTF-8''([^;]+)/i);
  if (m) { try { return decodeURIComponent(m[1]); } catch { /* fall through */ } }
  m = cd.match(/filename="?([^";]+)"?/i);
  if (m) return m[1].trim();
  try {
    const base = decodeURIComponent((new URL(url)).pathname.split("/").pop() || "");
    if (base) return base;
  } catch { /* ignore */ }
  return "document";
}

async function uploadFiles({ adam, files }) {
  const s = await getSettings();
  adam = (adam || "").trim();
  if (!adam) {
    return { ok: false, error: "No target act. Open an act's edit page in a tab, or type the ΑΔΑΜ." };
  }
  if (!files || !files.length) return { ok: false, error: "No files selected." };

  const fd = new FormData();
  const results = [];
  for (const f of files) {
    try {
      const resp = await fetch(f.url, { credentials: "include" });
      if (!resp.ok) { results.push({ url: f.url, ok: false, error: "download HTTP " + resp.status }); continue; }
      const buf = await resp.arrayBuffer();
      if (!buf.byteLength) { results.push({ url: f.url, ok: false, error: "empty file" }); continue; }
      const name = (f.name || filenameFor(resp, f.url)).replace(/[\\/]/g, "_");
      const type = resp.headers.get("content-type") || "application/octet-stream";
      fd.append("files", new File([buf], name, { type }));
      results.push({ url: f.url, ok: true, name, size: buf.byteLength });
    } catch (e) {
      results.push({ url: f.url, ok: false, error: String(e && e.message || e) });
    }
  }

  const okCount = results.filter(r => r.ok).length;
  if (!okCount) return { ok: false, error: "Could not download any of the selected files.", results };

  const url = `${s.baseUrl}/admin/act/${encodeURIComponent(adam)}/attachments`;
  const headers = {};
  const ah = authHeader(s);
  if (ah) headers["Authorization"] = ah;

  let resp;
  try {
    resp = await fetch(url, { method: "POST", body: fd, credentials: "include", headers });
  } catch (e) {
    return { ok: false, error: "Upload request failed (is the app running at " + s.baseUrl + "?): " + String(e && e.message || e), results };
  }
  if (resp.status === 401) {
    return { ok: false, error: "Not authenticated to the app (401). Log in on the app tab, or set credentials in the extension's Options.", results };
  }
  if (resp.status === 403) {
    return { ok: false, error: "Attachments are disabled on this app (ATTACHMENTS_ENABLED is off).", results };
  }
  if (resp.status === 404) {
    return { ok: false, error: `Act ${adam} not found on the app.`, results };
  }
  if (!resp.ok) {
    return { ok: false, error: `Upload failed: HTTP ${resp.status}.`, results };
  }
  return { ok: true, adam, uploaded: okCount, actUrl: `${s.baseUrl}/act/${encodeURIComponent(adam)}`, results };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg && msg.type === "getContext") {
        const s = await getSettings();
        sendResponse({
          baseUrl: s.baseUrl,
          adam: await detectActAdam(s.baseUrl),
          hasCreds: !!(s.authUser && s.authPass),
          optionsUrl: chrome.runtime.getURL("options.html"),
        });
      } else if (msg && msg.type === "openOptions") {
        // Must be opened from this privileged context: a content script can't
        // navigate the page to a chrome-extension:// URL (the browser blocks it).
        chrome.runtime.openOptionsPage();
        sendResponse({ ok: true });
      } else if (msg && msg.type === "upload") {
        sendResponse(await uploadFiles(msg));
      } else {
        sendResponse({ ok: false, error: "unknown message" });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message || e) });
    }
  })();
  return true; // keep the channel open for the async response
});

// Toolbar click → inject/toggle the picker on the current page. content.js
// guards against double-injection and toggles instead.
chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  } catch (e) {
    console.warn("[tender-docs] cannot run here (e.g. a chrome:// page):", e);
  }
});
