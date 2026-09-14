/* act_page.js — the act detail page's sections and its deadline badge.
 *
 * SECTIONS. The page ships every section in the HTML, stacked. This script
 * marks <html> with `js-tabs`, and only then does the CSS fold them: into tabs
 * on a desktop, into an accordion on a phone. With scripts off the page reads
 * top to bottom exactly as before — nothing is ever unreachable.
 *
 * Both layouts run off the same two classes on each section:
 *   .on    the one section the desktop tab bar shows
 *   .open  the sections a phone has unfolded (any number)
 * Keeping both in step means rotating a tablet never lands on a blank page.
 *
 * Two other scripts jump INTO a section (occurrences.js for search hits,
 * ai_summary.js for citations). They call window.actTabs.reveal(el) first,
 * because an element inside a hidden section has no position to scroll to.
 *
 * The open tab lives in the #hash, never in ?tab= — a query parameter would
 * make a second, non-canonical URL for the same act (app/seo.py).
 *
 * DEADLINE. "closes in N days" is counted from the viewer's own clock, so the
 * server never needs a notion of "today" and a cached page never goes stale.
 * It is derived only from the deadline already printed next to it.
 */
(function () {
  'use strict';

  var bar = document.getElementById('act-tabs');
  var panels = [].slice.call(document.querySelectorAll('.apanel'));
  if (bar && panels.length) {
    document.documentElement.classList.add('js-tabs');
    var buttons = [].slice.call(bar.querySelectorAll('.atab'));

    function panelOf(name) {
      return document.getElementById('tab-' + name);
    }

    function visibleNames() {
      return buttons.filter(function (b) { return !b.hidden; })
                    .map(function (b) { return b.getAttribute('data-tab'); });
    }

    // Opening the full-text section should show the text, not a second
    // "click to view" fold inside it.
    function unfoldInner(panel) {
      var d = panel.querySelector('details#ft-details');
      if (d && !d.open) d.open = true;
    }

    function setOpen(panel, open) {
      panel.classList.toggle('open', open);
      var tog = panel.querySelector('.apanel-toggle');
      if (tog) tog.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) unfoldInner(panel);
    }

    function show(name, writeHash) {
      var names = visibleNames();
      if (names.indexOf(name) === -1) name = names[0];
      buttons.forEach(function (b) {
        var on = b.getAttribute('data-tab') === name;
        b.classList.toggle('on', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.tabIndex = on ? 0 : -1;
      });
      panels.forEach(function (p) {
        p.classList.toggle('on', p.id === 'tab-' + name);
      });
      var panel = panelOf(name);
      if (panel) setOpen(panel, true);
      if (writeHash && window.history && history.replaceState) {
        history.replaceState(null, '', '#tab-' + name);
      }
    }

    window.actTabs = {
      reveal: function (el) {
        var panel = el && el.closest ? el.closest('.apanel') : null;
        if (panel) show(panel.getAttribute('data-tab'), false);
      }
    };

    bar.addEventListener('click', function (e) {
      var btn = e.target.closest('.atab');
      if (btn) show(btn.getAttribute('data-tab'), true);
    });

    // Left/right arrows move between tabs, as a tablist is expected to.
    bar.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      var live = buttons.filter(function (b) { return !b.hidden; });
      var i = live.indexOf(document.activeElement);
      if (i === -1) return;
      var next = live[(i + (e.key === 'ArrowRight' ? 1 : -1) + live.length) % live.length];
      next.focus();
      show(next.getAttribute('data-tab'), true);
      e.preventDefault();
    });

    // Phone accordion headers. Folding one never touches the others.
    panels.forEach(function (p) {
      var tog = p.querySelector('.apanel-toggle');
      if (!tog) return;
      tog.addEventListener('click', function () {
        var opening = !p.classList.contains('open');
        if (opening) show(p.getAttribute('data-tab'), true);
        else setOpen(p, false);
      });
    });

    // A section filled entirely over HTMX (competition, documents) may come
    // back empty: the panel routes answer "" when there is nothing to say. An
    // empty tab would be a dead end, so it disappears.
    function dropEmpty() {
      panels.forEach(function (p) {
        if (!p.hasAttribute('data-autohide') || p.hidden) return;
        var body = p.querySelector('.apanel-body');
        if (body && body.children.length === 0) {
          p.hidden = true;
          var b = bar.querySelector('[data-tab="' + p.getAttribute('data-tab') + '"]');
          if (b) b.hidden = true;
          if (p.classList.contains('on')) show(visibleNames()[0], false);
        }
      });
    }
    document.body.addEventListener('htmx:afterSettle', dropEmpty);

    // Where to start: #tab-<name>, or a #anchor that lives inside a section
    // (a shared link to ft-p-12), else the first section.
    var hash = (window.location.hash || '').replace(/^#/, '');
    var start = null;
    if (hash.indexOf('tab-') === 0) {
      start = hash.slice(4);
    } else if (hash) {
      var target = document.getElementById(hash);
      var owner = target && target.closest('.apanel');
      if (owner) {
        start = owner.getAttribute('data-tab');
        unfoldInner(owner);
      }
    }
    show(start || buttons[0].getAttribute('data-tab'), false);
  }

  // Print shows every section (see the print CSS); the button needs a script,
  // so it stays hidden without one.
  var printBtn = document.querySelector('[data-act-print]');
  if (printBtn) {
    printBtn.hidden = false;
    printBtn.addEventListener('click', function () { window.print(); });
  }

  var badge = document.querySelector('.dl-left[data-deadline]');
  if (badge) {
    var due = new Date(badge.getAttribute('data-deadline'));
    if (!isNaN(due.getTime())) {
      var now = new Date();
      var dayOf = function (d) { return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()); };
      var days = Math.round((dayOf(due) - dayOf(now)) / 86400000);
      var text, cls;
      if (due <= now) { text = badge.getAttribute('data-l-closed'); cls = 'closed'; }
      else if (days === 0) { text = badge.getAttribute('data-l-today'); cls = 'soon'; }
      else if (days === 1) { text = badge.getAttribute('data-l-tomorrow'); cls = 'soon'; }
      else {
        text = badge.getAttribute('data-l-days').replace('{n}', days);
        cls = days <= 7 ? 'soon' : 'later';
      }
      badge.textContent = text;
      badge.classList.add(cls);
      badge.hidden = false;
    }
  }
})();
