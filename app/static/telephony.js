/* telephony.js — WebRTC softphone + screen-pop client (CTI prototype).
 *
 * Fetches /telephony/config; if provisioned, registers a JsSIP UA to Asterisk
 * over WebSocket, opens /telephony/ws for CRM screen-pops, and exposes
 * window.khmdhsPhone.call(number, meta) for click-to-call. Phone numbers marked
 * up as <a href="tel:…"> or [data-call] anywhere in the app become clickable.
 *
 * Depends on JsSIP (vendored) and #tp-root markup (_softphone.html).
 */
(function () {
  "use strict";
  var root = document.getElementById("tp-root");
  if (!root || typeof JsSIP === "undefined") return;

  var I = window.TP_I18N || {};
  var $ = function (id) { return document.getElementById(id); };
  var audio = $("tp-audio");

  var ua = null;          // JsSIP.UA
  var session = null;     // active RTCSession
  var cfg = null;         // /telephony/config payload
  var popWs = null;       // screen-pop WebSocket
  var pendingPop = null;  // last screen-pop, matched to the ringing call by number
  var dialInfo = null;    // {number, name, customer_user_id} for an outgoing call
  var callStart = 0, timer = null, muted = false;
  // Short-lived SIP credential refresh (SIP_AUTH_MODE=realtime): /config returns
  // sip_password_ttl and we re-register with a fresh secret before it expires.
  var refreshTimer = null, pendingCfg = null, lastRefreshAt = 0;

  // ---- view helpers ------------------------------------------------------ //
  function setReg(state, text) {
    var cls = state === "on" ? "tp-dot-on" : state === "busy" ? "tp-dot-busy" : "tp-dot-off";
    ["tp-reg-dot", "tp-fab-dot"].forEach(function (id) {
      var d = $(id); if (d) d.className = "tp-dot " + cls;
    });
    $("tp-reg-text").textContent = text || "";
  }
  function showBody(which) {
    ["tp-idle", "tp-incoming", "tp-active"].forEach(function (id) {
      $(id).hidden = (id !== "tp-" + which);
    });
  }
  function openPanel() { $("tp-panel").hidden = false; $("tp-fab").hidden = true; }
  function closePanel() { $("tp-panel").hidden = true; $("tp-fab").hidden = false; }

  function fmtDur(sec) {
    var m = Math.floor(sec / 60), s = sec % 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }
  function startTimer() {
    stopTimer();
    timer = setInterval(function () {
      $("tp-timer").textContent = fmtDur(Math.floor((Date.now() - callStart) / 1000));
    }, 500);
  }
  function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }

  // ---- caller identity rendering ----------------------------------------- //
  function applyIncoming(number, match) {
    $("tp-in-num").textContent = number || "";
    $("tp-in-name").textContent = (match && match.name) || I.unknown || number || "";
    $("tp-in-sub").textContent = (match && match.subtitle) || "";
    var link = $("tp-in-link");
    if (match && match.url) { link.href = match.url; link.hidden = false; }
    else { link.hidden = true; }
  }
  function applyActive(number, name, sub) {
    $("tp-act-num").textContent = number || "";
    $("tp-act-name").textContent = name || number || "";
    $("tp-act-sub").textContent = sub || "";
  }

  // ---- bootstrap --------------------------------------------------------- //
  fetch(root.dataset.configUrl, { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : {}; })
    .then(function (c) {
      if (!c || !c.enabled || !c.provisioned) return;  // stay hidden — no-op
      cfg = c;
      $("tp-ext").textContent = c.extension || "";
      root.hidden = false;
      $("tp-fab").hidden = false;
      startUA(c);
      connectPop();
      scheduleRefresh(c.sip_password_ttl);
    })
    .catch(function () { /* telephony simply unavailable */ });

  // ---- short-lived credential refresh ------------------------------------ //
  function scheduleRefresh(ttl) {
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
    if (!ttl || ttl <= 0) return;                 // static mode: no rotation
    // Refresh at 80% of the TTL, but never sooner than 15s.
    var ms = Math.max(15, Math.floor(ttl * 0.8)) * 1000;
    refreshTimer = setTimeout(refreshConfig, ms);
  }

  function refreshConfig() {
    lastRefreshAt = Date.now();
    fetch(root.dataset.configUrl, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (c) {
        if (!c || !c.enabled || !c.provisioned) return;
        var changed = !cfg || c.sip_password !== cfg.sip_password;
        if (changed) applyNewCfg(c);
        else cfg = c;                              // keep ws_token/ttl fresh
        scheduleRefresh(c.sip_password_ttl);
      })
      .catch(function () { scheduleRefresh(60); });  // transient: retry in ~48s
  }

  // Re-register with a new secret. JsSIP fixes credentials at UA construction,
  // so this is stop-then-start. An active call must survive it, so defer the
  // restart to endCall() when one is in progress (out-of-dialog anyway).
  function applyNewCfg(c) {
    if (session) { pendingCfg = c; return; }
    cfg = c;
    try { if (ua) ua.stop(); } catch (_) {}
    startUA(c);
  }

  // ---- SIP UA ------------------------------------------------------------ //
  function startUA(c) {
    setReg("busy", I.connecting);
    var socket = new JsSIP.WebSocketInterface(c.ws_url);
    ua = new JsSIP.UA({
      sockets: [socket],
      uri: "sip:" + c.sip_user + "@" + c.domain,
      password: c.sip_password,
      display_name: c.display_name || c.sip_user,
      register: true,
      session_timers: false
    });
    ua.on("connecting", function () { setReg("busy", I.connecting); });
    ua.on("registered", function () { setReg("on", I.registered); });
    ua.on("unregistered", function () { setReg("off", I.offline); });
    ua.on("registrationFailed", function () {
      setReg("off", I.offline);
      // A short-lived secret may have expired while the tab slept — refetch once
      // (debounced) and re-register when idle. No-op in static mode (no TTL).
      if (cfg && cfg.sip_password_ttl && Date.now() - lastRefreshAt > 5000) refreshConfig();
    });
    ua.on("disconnected", function () { setReg("off", I.offline); });
    ua.on("newRTCSession", onSession);
    ua.start();
  }

  function attachMedia(pc) {
    if (!pc) return;
    pc.addEventListener("track", function (e) {
      if (e.streams && e.streams[0]) audio.srcObject = e.streams[0];
    });
    // Legacy fallback for older WebRTC stacks.
    pc.addEventListener("addstream", function (e) { audio.srcObject = e.stream; });
  }

  function onSession(e) {
    var s = e.session;
    if (session && session !== s) { try { s.terminate(); } catch (_) {} return; }
    session = s;
    muted = false;

    s.on("peerconnection", function (ev) { attachMedia(ev.peerconnection); });
    if (s.connection) attachMedia(s.connection);

    s.on("accepted", onConnected);
    s.on("confirmed", onConnected);
    s.on("ended", endCall);
    s.on("failed", function (ev) {
      if (ev && ev.cause && /User Media|getUserMedia|NotAllowed/i.test(String(ev.cause))) {
        alert(I.mic_denied || "Microphone unavailable");
      }
      endCall();
    });

    if (s.direction === "incoming") {
      hideNotify();  // we ARE the party ringing — the full card supersedes the toast
      var num = (s.remote_identity && s.remote_identity.uri && s.remote_identity.uri.user) || "";
      var match = (pendingPop && pendingPop.raw_number &&
        endsWith(num, pendingPop.raw_number)) ? pendingPop.match :
        (pendingPop ? pendingPop.match : null);
      dialInfo = { number: num, customer_user_id: match ? match.customer_user_id : null };
      applyIncoming((pendingPop && pendingPop.number) || num, match);
      openPanel();
      showBody("incoming");
    } else {
      applyActive(dialInfo && dialInfo.number, dialInfo && dialInfo.name, I.calling);
      openPanel();
      showBody("active");
    }
  }

  function onConnected() {
    if (!callStart) { callStart = Date.now(); startTimer(); }
    if (dialInfo) applyActive(dialInfo.number, dialInfo.name, I.in_call);
    setReg("busy", I.in_call);
    showBody("active");
  }

  // ---- outgoing ---------------------------------------------------------- //
  function dial(number) {
    if (!ua || !number) return;
    var clean = String(number).replace(/[^\d+*#]/g, "");
    var target = "sip:" + clean + "@" + cfg.domain;
    try {
      ua.call(target, { mediaConstraints: { audio: true, video: false } });
    } catch (err) {
      alert(I.mic_denied || String(err));
    }
  }

  // ---- recognition toast (for managers/admins not answering) ------------- //
  var notifyTimer = null;
  function showNotify(m) {
    var match = m.match;
    $("tp-notify-name").textContent = (match && match.name) || I.unknown || m.number || "";
    $("tp-notify-sub").textContent = (match && match.subtitle) || "";
    $("tp-notify-num").textContent = m.number || "";
    var link = $("tp-notify-link");
    if (match && match.url) { link.href = match.url; link.hidden = false; }
    else { link.hidden = true; }
    $("tp-notify").hidden = false;
    if (notifyTimer) clearTimeout(notifyTimer);
    notifyTimer = setTimeout(hideNotify, 20000);  // auto-dismiss
  }
  function hideNotify() {
    if (notifyTimer) { clearTimeout(notifyTimer); notifyTimer = null; }
    $("tp-notify").hidden = true;
  }

  // ---- screen-pop socket ------------------------------------------------- //
  function connectPop() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + location.host + root.dataset.wsUrl;
    // Auth via signed token from /config — the session cookie isn't reliably
    // sent on the WS handshake behind proxies/iframes.
    if (cfg && cfg.ws_token) url += "?token=" + encodeURIComponent(cfg.ws_token);
    try {
      popWs = new WebSocket(url);
    } catch (_) { return; }
    popWs.onmessage = function (ev) {
      var m; try { m = JSON.parse(ev.data); } catch (_) { return; }
      if (m.type !== "incoming") return;
      pendingPop = m;
      if (session && session.direction === "incoming") {
        // This browser is the one ringing — enrich the incoming card.
        applyIncoming(m.number, m.match);
        dialInfo = { number: m.raw_number || m.number,
                     customer_user_id: m.match ? m.match.customer_user_id : null };
      } else {
        // Routed here as recognition (assigned manager / admin) but we're not
        // the party answering — show a lightweight notification toast.
        showNotify(m);
      }
    };
    popWs.onclose = function () { setTimeout(connectPop, 3000); };
    popWs.onerror = function () { try { popWs.close(); } catch (_) {} };
  }

  // ---- call teardown + logging ------------------------------------------ //
  function endCall() {
    stopTimer();
    var dur = callStart ? Math.floor((Date.now() - callStart) / 1000) : 0;
    var incoming = session && session.direction === "incoming";
    if (callStart && dialInfo && dialInfo.customer_user_id) {
      fetch(root.dataset.logUrl, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customer_user_id: dialInfo.customer_user_id,
          external_number: dialInfo.number,
          direction: incoming ? "incoming" : "outgoing",
          status: "held", duration_s: dur
        })
      }).catch(function () {});
    }
    session = null; dialInfo = null; callStart = 0; muted = false; pendingPop = null;
    $("tp-timer").textContent = "00:00";
    $("tp-mute").classList.remove("tp-on");
    $("tp-mute").textContent = I.mute || "Mute";
    setReg(ua && ua.isRegistered && ua.isRegistered() ? "on" : "off",
           ua && ua.isRegistered && ua.isRegistered() ? I.registered : I.offline);
    showBody("idle");
    // Apply a credential refresh that arrived mid-call, now that we're idle.
    if (pendingCfg) { var nc = pendingCfg; pendingCfg = null; applyNewCfg(nc); }
  }

  // ---- controls ---------------------------------------------------------- //
  $("tp-fab").addEventListener("click", openPanel);
  $("tp-close").addEventListener("click", closePanel);
  $("tp-notify-x").addEventListener("click", hideNotify);
  $("tp-notify-link").addEventListener("click", hideNotify);
  $("tp-dial").addEventListener("click", function () {
    var n = $("tp-num").value.trim();
    if (n) { dialInfo = { number: n }; dial(n); }
  });
  $("tp-num").addEventListener("keydown", function (e) {
    if (e.key === "Enter") $("tp-dial").click();
  });
  $("tp-answer").addEventListener("click", function () {
    if (session) try { session.answer({ mediaConstraints: { audio: true, video: false } }); } catch (_) {}
  });
  $("tp-decline").addEventListener("click", function () {
    if (session) try { session.terminate(); } catch (_) {}
  });
  $("tp-hang").addEventListener("click", function () {
    if (session) try { session.terminate(); } catch (_) {}
  });
  $("tp-mute").addEventListener("click", function () {
    if (!session) return;
    muted = !muted;
    try { muted ? session.mute({ audio: true }) : session.unmute({ audio: true }); } catch (_) {}
    var b = $("tp-mute");
    b.classList.toggle("tp-on", muted);
    b.textContent = muted ? (I.unmute || "Unmute") : (I.mute || "Mute");
  });

  // live lookup hint under the dial input (debounced)
  var hintT = null;
  $("tp-num").addEventListener("input", function () {
    var n = this.value.trim();
    $("tp-num-match").textContent = "";
    if (hintT) clearTimeout(hintT);
    if (n.replace(/\D/g, "").length < 6) return;
    hintT = setTimeout(function () {
      fetch(root.dataset.lookupUrl + "?number=" + encodeURIComponent(n),
            { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.match) $("tp-num-match").textContent =
            d.match.name + (d.match.subtitle ? " · " + d.match.subtitle : "");
        }).catch(function () {});
    }, 350);
  });

  // ---- click-to-call ----------------------------------------------------- //
  window.khmdhsPhone = {
    call: function (number, meta) {
      dialInfo = Object.assign({ number: number }, meta || {});
      openPanel();
      dial(number);
    },
    ready: function () { return !!(ua && ua.isRegistered && ua.isRegistered()); }
  };

  document.addEventListener("click", function (ev) {
    var t = ev.target.closest && ev.target.closest('a[href^="tel:"],[data-call]');
    if (!t) return;
    var num = t.getAttribute("data-call") || (t.getAttribute("href") || "").replace(/^tel:/, "");
    if (!num) return;
    ev.preventDefault();
    window.khmdhsPhone.call(num, {
      name: t.getAttribute("data-name") || null,
      customer_user_id: t.getAttribute("data-customer-id") || null
    });
  });

  function endsWith(a, b) {
    a = String(a).replace(/\D/g, ""); b = String(b).replace(/\D/g, "");
    if (!a || !b) return false;
    var n = Math.min(a.length, b.length, 9);
    return a.slice(-n) === b.slice(-n);
  }
})();
