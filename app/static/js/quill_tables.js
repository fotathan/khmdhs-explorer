/* quill_tables.js — table editing for the full-text Quill editors.
 *
 * Quill 2.0.3 (app/static/vendor) ships a table module but no toolbar for it.
 * This wires a small button bar (_quill_table_bar.html, labels translated
 * server-side) to that module. Both full-text editors use it: the act form
 * (_act_form_body.html) and the /tables + edit-hub editor
 * (tables/_fulltext_editor.html). The editor must be created with
 * modules: { table: true }.
 *
 * Two things that look odd and are load-bearing:
 *  - Buttons cancel mousedown. The table module acts on the CURRENT selection;
 *    a normal click moves focus to the button first and the selection is gone.
 *  - Row/column buttons are disabled outside a table, because the module's
 *    methods silently do nothing there — a button that does nothing reads as
 *    broken.
 */
(function () {
  if (window.khmdhsQuillTables) return;

  var ACTIONS = {
    'insert':    function (t) { t.insertTable(3, 3); },
    'row-above': function (t) { t.insertRowAbove(); },
    'row-below': function (t) { t.insertRowBelow(); },
    'col-left':  function (t) { t.insertColumnLeft(); },
    'col-right': function (t) { t.insertColumnRight(); },
    'del-row':   function (t) { t.deleteRow(); },
    'del-col':   function (t) { t.deleteColumn(); },
    'del-table': function (t) { t.deleteTable(); }
  };

  function inTable(quill, table) {
    var range = quill.getSelection();
    if (!range) return false;
    var found = table.getTable(range);
    return !!(found && found[0]);
  }

  function attach(quill, bar) {
    quill.root.classList.add('rich-text');
    if (!bar || bar.__qtAttached) return;
    var table = quill.getModule('table');
    if (!table) return;                       // editor built without the module
    bar.__qtAttached = true;

    // Quill puts its toolbar immediately before the editor; sit right after it.
    quill.container.parentNode.insertBefore(bar, quill.container);
    bar.hidden = false;

    var buttons = Array.prototype.slice.call(bar.querySelectorAll('button[data-tbl]'));
    var lastInside = false;
    function refresh() {
      // Keep the last answer while the editor is blurred, so tabbing to a
      // button does not disable it on the way.
      if (quill.hasFocus()) lastInside = inTable(quill, table);
      buttons.forEach(function (b) {
        if (b.getAttribute('data-tbl') !== 'insert') b.disabled = !lastInside;
      });
    }

    buttons.forEach(function (b) {
      b.addEventListener('mousedown', function (e) { e.preventDefault(); });
      b.addEventListener('click', function (e) {
        e.preventDefault();
        var act = ACTIONS[b.getAttribute('data-tbl')];
        if (!act) return;
        if (!quill.hasFocus()) quill.focus();
        act(table);
        refresh();
      });
    });
    quill.on('editor-change', refresh);
    refresh();
  }

  window.khmdhsQuillTables = { attach: attach };
})();
