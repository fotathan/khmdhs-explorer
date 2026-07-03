const DEFAULTS = { baseUrl: "http://localhost:8000", authUser: "", authPass: "" };

function $(id) { return document.getElementById(id); }

async function load() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  $("baseUrl").value = s.baseUrl;
  $("authUser").value = s.authUser;
  $("authPass").value = s.authPass;
}

async function save() {
  const baseUrl = $("baseUrl").value.trim().replace(/\/+$/, "") || DEFAULTS.baseUrl;
  await chrome.storage.sync.set({
    baseUrl,
    authUser: $("authUser").value.trim(),
    authPass: $("authPass").value,
  });
  const el = $("saved");
  el.textContent = "Saved ✓";
  setTimeout(() => { el.textContent = ""; }, 1800);
}

$("save").addEventListener("click", save);
load();
