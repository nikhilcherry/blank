// Everything the report needs at runtime: theme toggle, table sort, filter.
// ~60 lines of vanilla JS, inlined into the report. No dependencies.
(function () {
  "use strict";

  var root = document.documentElement;
  var KEY = "blank-theme";

  function apply(theme) {
    if (theme) root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }

  function current() {
    var stored = null;
    try { stored = localStorage.getItem(KEY); } catch (e) { /* file:// with no storage */ }
    if (stored) return stored;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  apply(localStorageSafe());

  function localStorageSafe() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }

  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      apply(next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* ignore */ }
      toggle.textContent = next === "dark" ? "☀ Light" : "☾ Dark";
    });
    toggle.textContent = current() === "dark" ? "☀ Light" : "☾ Dark";
  }

  // Sortable tables: <th data-sort="num|text">, cells carry data-v when the
  // display text is not the sort key.
  document.querySelectorAll("table[data-sortable]").forEach(function (table) {
    var headers = table.querySelectorAll("th[data-sort]");
    headers.forEach(function (th, index) {
      th.addEventListener("click", function () {
        var numeric = th.dataset.sort === "num";
        var asc = !(th.classList.contains("sorted") && !th.classList.contains("asc"));
        // First click on a numeric column sorts high-to-low; text sorts A-Z.
        if (!th.classList.contains("sorted")) asc = !numeric;
        headers.forEach(function (h) { h.classList.remove("sorted", "asc"); });
        th.classList.add("sorted");
        if (asc) th.classList.add("asc");

        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var x = key(a.cells[index], numeric);
          var y = key(b.cells[index], numeric);
          if (x < y) return asc ? -1 : 1;
          if (x > y) return asc ? 1 : -1;
          return 0;
        });
        rows.forEach(function (row) { body.appendChild(row); });
      });
    });
  });

  function key(cell, numeric) {
    if (!cell) return numeric ? -Infinity : "";
    var raw = cell.dataset.v !== undefined ? cell.dataset.v : cell.textContent;
    if (!numeric) return raw.trim().toLowerCase();
    var n = parseFloat(String(raw).replace(/[^0-9.eE+-]/g, ""));
    return isNaN(n) ? -Infinity : n;
  }

  // Live filter: <input data-filter="#table-id">
  document.querySelectorAll("input[data-filter]").forEach(function (input) {
    input.addEventListener("input", function () {
      var table = document.querySelector(input.dataset.filter);
      if (!table) return;
      var needle = input.value.trim().toLowerCase();
      var shown = 0;
      Array.prototype.forEach.call(table.tBodies[0].rows, function (row) {
        var hit = !needle || row.textContent.toLowerCase().indexOf(needle) !== -1;
        row.style.display = hit ? "" : "none";
        if (hit) shown++;
      });
      var counter = document.querySelector(input.dataset.count || "#nothing");
      if (counter) counter.textContent = shown + " shown";
    });
  });
})();
