"""Self-contained local test console for the native mobile API."""


def html() -> str:
    # No CDN assets: this page must also work in restricted in-app browsers.
    return r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KHMDHS Mobile API Demo</title>
<style>
:root{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172033;background:#eef2f8}
*{box-sizing:border-box}body{margin:0}.shell{max-width:1040px;margin:auto;padding:28px 18px 60px}
h1{font-size:28px;margin:0 0 6px}.sub{color:#5c6880;margin:0 0 24px}.panel,.card{background:white;border:1px solid #dbe2ee;border-radius:16px;box-shadow:0 5px 18px #2d42600d}
.panel{padding:20px;margin-bottom:18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.card{padding:17px}
label{display:block;font-size:13px;font-weight:650;margin:10px 0 5px}input,select{width:100%;padding:11px 12px;border:1px solid #c7d0df;border-radius:10px;font-size:15px}
button{border:0;border-radius:10px;padding:11px 15px;font-weight:700;cursor:pointer;background:#2957d3;color:white;margin-top:14px}button.secondary{background:#e8eefc;color:#2347a6;margin-left:7px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.hidden{display:none}.status{margin-top:12px;font-size:14px}.ok{color:#167346}.bad{color:#b52828}
.metric{font-size:28px;font-weight:760;margin:5px 0}.muted{color:#6a7589;font-size:13px}.item{padding:11px 0;border-top:1px solid #edf0f5}.item:first-child{border-top:0}.item b{display:block;margin-bottom:3px}
.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#edf2ff;color:#3159bd;font-size:12px;font-weight:700;margin-bottom:8px}
@media(max-width:620px){.shell{padding:18px 12px}.row{grid-template-columns:1fr}h1{font-size:24px}}
</style></head><body><main class="shell">
<h1>KHMDHS mobile demo</h1><p class="sub">A local preview of the mobile data and notification services built so far.</p>
<section id="loginPanel" class="panel"><h2>Demo sign in</h2>
<div class="row"><div><label>Username</label><input id="username" value="mobile_demo"></div>
<div><label>Password</label><input id="password" type="password" value="MobileDemo-2026!"></div></div>
<button onclick="login()">Sign in and load demo</button><div id="loginStatus" class="status"></div></section>
<section id="dashboard" class="hidden">
<div class="grid">
 <article class="card"><span class="pill">Account</span><h3 id="accountName">—</h3><div id="accountState" class="muted"></div></article>
 <article class="card"><span class="pill">Saved searches</span><div id="savedCount" class="metric">0</div><div id="savedNames" class="muted"></div></article>
 <article class="card"><span class="pill">Opportunities</span><div id="resultCount" class="metric">0</div><div class="muted">Sample procurement results</div></article>
</div>
<div class="grid" style="margin-top:14px">
 <article class="card"><h3>Notification settings</h3>
  <label><input id="paused" type="checkbox" style="width:auto"> Pause all notifications</label>
  <div class="row"><div><label>Quiet from</label><input id="quietStart" value="22:00"></div><div><label>Quiet until</label><input id="quietEnd" value="08:00"></div></div>
  <div class="row"><div><label>Daily summary</label><input id="summaryTime" value="08:30"></div><div><label>Daily cap</label><input id="dailyCap" type="number" min="1" max="10" value="6"></div></div>
  <button onclick="saveSettings()">Save settings</button><div id="settingsStatus" class="status"></div>
 </article>
 <article class="card"><h3>Notification inbox</h3><div id="inbox"></div>
  <button class="secondary" onclick="markAllRead()">Mark all read</button>
 </article>
</div>
<article class="card" style="margin-top:14px"><h3>Latest opportunities</h3><div id="results"></div></article>
</section></main>
<script>
let token='';let settings=null;
const device={installation_id:'b0f29d79-7e1f-4ec9-84c1-b4f8eb0798e2',platform:'ios',app_version:'0.5.0',os_version:'demo',locale:'el',timezone:'Europe/Athens',display_name:'Demo iPhone'};
async function api(path,options={}){options.headers={...(options.headers||{}),...(token?{Authorization:'Bearer '+token}:{})};if(options.body)options.headers['Content-Type']='application/json';let r=await fetch('/api/v1'+path,options);let body=r.status===204?null:await r.json();if(!r.ok)throw new Error(body?.error?.message||('Request failed: '+r.status));return body}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function login(){let s=document.getElementById('loginStatus');s.textContent='Signing in…';s.className='status';try{let r=await api('/auth/login',{method:'POST',body:JSON.stringify({username:username.value,password:password.value,device})});token=r.access_token;s.textContent='Signed in';s.className='status ok';await load()}catch(e){s.textContent=e.message;s.className='status bad'}}
async function load(){let [me,prefs,saved,notes,search]=await Promise.all([api('/me'),api('/notification-settings'),api('/saved-searches'),api('/notifications'),api('/acts/search',{method:'POST',body:JSON.stringify({filters:{},limit:10})})]);settings=prefs;loginPanel.classList.add('hidden');dashboard.classList.remove('hidden');accountName.textContent=me.username;accountState.textContent=me.entitlement.has_access?'Active customer access':'Access inactive';savedCount.textContent=saved.items.length;savedNames.textContent=saved.items.map(x=>x.name).join(', ')||'None';resultCount.textContent=search.items.length;paused.checked=prefs.paused;quietStart.value=prefs.quiet_start;quietEnd.value=prefs.quiet_end;summaryTime.value=prefs.summary_time;dailyCap.value=prefs.daily_cap;renderInbox(notes);renderResults(search.items)}
function renderInbox(n){inbox.innerHTML=n.items.map(x=>`<div class="item"><b>${esc(x.title)}</b><div>${esc(x.body)}</div><span class="muted">${x.read?'Read':'Unread'} · ${esc(x.type)}</span></div>`).join('')||'<div class="muted">No notifications</div>'}
function renderResults(items){results.innerHTML=items.map(x=>`<div class="item"><b>${esc(x.title)}</b><div>${esc(x.authority?.name||'')} · ${esc(x.value?.amount||'—')} EUR</div><span class="muted">${esc(x.adam)} · ${esc(x.type.label)}</span></div>`).join('')}
async function saveSettings(){let s=document.getElementById('settingsStatus');try{settings=await api('/notification-settings',{method:'PUT',body:JSON.stringify({paused:paused.checked,timezone:settings.timezone,quiet_start:quietStart.value,quiet_end:quietEnd.value,summary_time:summaryTime.value,daily_cap:Number(dailyCap.value),language:settings.language})});s.textContent='Settings saved';s.className='status ok'}catch(e){s.textContent=e.message;s.className='status bad'}}
async function markAllRead(){try{await api('/notifications/read-all',{method:'POST'});renderInbox(await api('/notifications'))}catch(e){alert(e.message)}}
</script></body></html>"""
