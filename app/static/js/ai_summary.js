/* ai_summary.js — the click-through from an extracted item to the sentence
 * that produced it.
 *
 * The server has already done the hard part. Every item the panel renders
 * survived the quote gate, which means its quote was located in the source and
 * its character offset mapped to a paragraph that exists in this page. All that
 * is left is to open whatever is hiding it, scroll there and flash it — exactly
 * what occurrences.js does for search hits, against the same `ft-p-<n>` ids and
 * the same .occ-flash animation. Two surfaces, one numbering.
 *
 * Delegated from the document, because the panel arrives over HTMX and is
 * replaced again on every poll: a listener bound to the buttons themselves
 * would be thrown away with the first swap.
 */
(function () {
  'use strict';

  function goTo(anchor) {
    var target = document.getElementById(anchor);
    if (!target) return;
    // The full text lives in a collapsed <details>; an element inside a closed
    // one has no layout, so open it BEFORE measuring where to scroll.
    var box = target.closest ? target.closest('details') : null;
    if (box && !box.open) box.open = true;
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.remove('occ-flash');
    void target.offsetWidth;             // restart the animation on a repeat click
    target.classList.add('occ-flash');
    window.setTimeout(function () { target.classList.remove('occ-flash'); }, 1200);
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('[data-ai-anchor]') : null;
    if (!btn) return;
    e.preventDefault();
    goTo(btn.getAttribute('data-ai-anchor'));
  });
})();
