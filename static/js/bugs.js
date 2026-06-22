/* Filtrage en direct de la grille de bugs (côté client, sans rechargement). */
(function () {
  "use strict";

  const search = document.getElementById("search");
  const filterState = document.getElementById("filter-state");
  const filterType = document.getElementById("filter-type");
  const filterArchived = document.getElementById("filter-archived");
  const sortBy = document.getElementById("sort-by");
  const sortDir = document.getElementById("sort-dir");
  const grid = document.getElementById("grid");
  const noResults = document.getElementById("no-results");
  const resultCount = document.getElementById("result-count");
  const HIDE_ARCHIVED_KEY = "bugtrack-hide-archived";

  if (!grid) return; // page "aucun bug"

  const cards = Array.prototype.slice.call(grid.querySelectorAll(".card"));
  const total = cards.length;
  let dir = "desc";

  /* Tri par critère noté sur 5 (importance produit, urgence, etc.). */
  function critVal(card, key) {
    return parseInt(card.getAttribute("data-crit-" + key), 10) || 0;
  }

  function applySort() {
    const key = sortBy ? sortBy.value : "";
    const ordered = key
      ? cards.slice().sort(function (a, b) {
          const diff = critVal(b, key) - critVal(a, key);
          return dir === "desc" ? diff : -diff;
        })
      : cards;
    ordered.forEach(function (card) { grid.appendChild(card); });
  }

  function apply() {
    const q = (search ? search.value : "").trim().toLowerCase();
    const st = filterState ? filterState.value : "";
    const tp = filterType ? filterType.value : "";
    const hideArchived = filterArchived ? filterArchived.checked : false;
    let shown = 0;

    cards.forEach(function (card) {
      const hay = card.getAttribute("data-search") || "";
      const matchText = !q || hay.indexOf(q) !== -1;
      const matchState = !st || card.getAttribute("data-state") === st;
      const matchType = !tp || card.getAttribute("data-type") === tp;
      const matchArchived = !hideArchived || card.getAttribute("data-archived") !== "1";
      const visible = matchText && matchState && matchType && matchArchived;
      card.style.display = visible ? "" : "none";
      if (visible) shown++;
    });

    if (noResults) noResults.style.display = shown === 0 ? "" : "none";
    if (resultCount) {
      const filtering = q || st || tp || hideArchived;
      resultCount.textContent = filtering
        ? shown + " / " + total + " affiché" + (shown > 1 ? "s" : "")
        : "";
    }
  }

  if (filterArchived) {
    try { filterArchived.checked = localStorage.getItem(HIDE_ARCHIVED_KEY) === "1"; } catch (e) {}
    filterArchived.addEventListener("change", function () {
      try { localStorage.setItem(HIDE_ARCHIVED_KEY, filterArchived.checked ? "1" : "0"); } catch (e) {}
      apply();
    });
  }

  if (search) search.addEventListener("input", apply);
  if (filterState) filterState.addEventListener("change", apply);
  if (filterType) filterType.addEventListener("change", apply);
  if (sortBy) sortBy.addEventListener("change", applySort);
  if (sortDir) {
    sortDir.addEventListener("click", function () {
      dir = dir === "desc" ? "asc" : "desc";
      sortDir.textContent = dir === "desc" ? "↓" : "↑";
      sortDir.setAttribute("data-dir", dir);
      applySort();
    });
  }

  apply();
  applySort();
})();
