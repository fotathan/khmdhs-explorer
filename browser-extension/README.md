# Tender Docs → Act (Chrome extension)

Pick tender documents on any awarding-authority page and upload them straight to
an open act in the KHMDHS explorer — no manual download / zip / drag-and-drop.

It reuses the app's existing upload endpoint (`POST /admin/act/{adam}/attachments`,
which already accepts multiple files and unpacks zips for search), so **no
changes to the web app are required**.

## What it does

1. You open an act's **edit** page in one tab (a *saved* act — attachments attach
   to an ΑΔΑΜ).
2. You go to the authority's tender page in another tab and click the extension
   icon. A panel lists every document link (PDF / Word / Excel / zip / …).
3. You tick the ones you want; the target act is auto-filled from the open edit
   tab (or type the ΑΔΑΜ).
4. **Upload** → the extension downloads each file *in your browser session* (so
   login-gated portals work), then POSTs them to the act. They land as normal
   attachments, searchable like any other.

## Install (unpacked, for internal use)

1. Chrome → `chrome://extensions` → enable **Developer mode** (top-right).
2. **Load unpacked** → select this `browser-extension/` folder.
3. Click the extension's **Details → Extension options** and set the **App base
   URL** (default `http://localhost:8000`; use your Render URL for production).

No Chrome Web Store listing is needed. To update, edit the files and hit
**Reload** on the extensions page.

## Notes & limits

- **Attachments must be enabled on the target app.** Local dev: run with
  `ATTACHMENTS_ENABLED=1`. On production it's currently off (free-tier space), so
  uploads there return 403 until that's flipped — nothing in the extension needs
  to change when it is.
- **Auth.** If you're logged into the app in a tab, the browser reuses that
  session automatically. If uploads return **401**, set Basic-Auth credentials in
  the extension's Options.
- **Saved acts only (v1).** Attachments need an ΑΔΑΜ, which exists once the act is
  saved. For a brand-new act, save it first, then upload. (Injecting into an
  unsaved create form is a possible v2.)
- **Permissions.** It requests `<all_urls>` so the background can download
  documents from arbitrary authority hosts (cross-origin fetch, which content
  scripts can't do). It only acts when you click the icon.

## Files

- `manifest.json` — MV3 manifest.
- `background.js` — detects the open act, downloads the selected docs, POSTs them.
- `content.js` — the in-page picker panel (injected on icon click).
- `options.html` / `options.js` — base URL + optional credentials.
- `icons/` — toolbar icons.
