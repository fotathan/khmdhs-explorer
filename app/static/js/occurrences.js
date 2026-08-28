/* Occurrence navigator — the client half of "Γιατί ταιριάζει".
 *
 * The server has already done the hard part: every occurrence the popover
 * lists carries the id of a <mark> that exists in this page. All that is left
 * is to open whatever is hiding it, scroll there and flash it.
 *
 * The one piece of state is the chip that opened the popover, so Esc can give
 * focus back to it. No framework, no store.
 */
(function () {
  'use strict';

  var pop = document.getElementById('occ-popover');
  if (!pop) return;

  var opener = null;               // the chip button the popover belongs to

  function close() {
    pop.hidden = true;
    if (opener) {
      opener.setAttribute('aria-expanded', 'false');
      opener.focus();
      opener = null;
    }
  }

  function place(chip) {
    // Anchored under the chip, nudged left if it would run off the viewport.
    var r = chip.getBoundingClientRect();
    var top = r.bottom + window.scrollY + 6;
    var left = r.left + window.scrollX;
    pop.style.top = top + 'px';
    pop.hidden = false;            // must be visible before we can measure it
    var overflow = (left + pop.offsetWidth) - (window.scrollX + document.documentElement.clientWidth - 12);
    pop.style.left = Math.max(12, overflow > 0 ? left - overflow : left) + 'px';
  }

  function items() {
    return Array.prototype.slice.call(pop.querySelectorAll('.occ-item'));
  }

  function goTo(anchor) {
    // The full text lives in a collapsed <details>; an element inside a closed
    // one has no layout, so open it BEFORE measuring where to scroll.
    var target = document.getElementById(anchor);
    if (!target) return;
    var box = target.closest('details');
    if (box && !box.open) box.open = true;
    close();
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.remove('occ-flash');
    void target.offsetWidth;       // restart the animation on a repeat click
    target.classList.add('occ-flash');
    window.setTimeout(function () { target.classList.remove('occ-flash'); }, 1200);
  }

  // A chip was clicked: remember it, and position the popover once HTMX has
  // filled it in.
  document.addEventListener('click', function (e) {
    var chip = e.target.closest ? e.target.closest('[data-occ-term]') : null;
    if (chip) { opener = chip; return; }
    if (!pop.hidden && !pop.contains(e.target)) close();
  });

  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.target !== pop || !opener) return;
    opener.setAttribute('aria-expanded', 'true');
    place(opener);
    var first = items()[0];
    if (first) first.focus();
  });

  pop.addEventListener('click', function (e) {
    if (e.target.closest('[data-occ-close]')) { close(); return; }
    var item = e.target.closest('.occ-item');
    if (item) goTo(item.getAttribute('data-occ-anchor'));
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !pop.hidden) { close(); return; }
    if (pop.hidden || !pop.contains(document.activeElement)) return;
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    var list = items();
    var i = list.indexOf(document.activeElement);
    var next = e.key === 'ArrowDown' ? i + 1 : i - 1;
    if (next >= 0 && next < list.length) list[next].focus();
  });
})();
