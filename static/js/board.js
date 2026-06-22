/* Tableau (Kanban) : glisser-déposer, colonnes, recherche d'ajout de bug. */
(function () {
  "use strict";

  const board = document.getElementById("board");
  if (!board) return;

  const addColBlock = board.querySelector(".add-col");

  // Carte d'identité des bugs (id -> infos) pour la recherche + l'exclusion.
  const BUGS = Array.isArray(window.__BUGS__) ? window.__BUGS__ : [];
  const projectOf = {};
  BUGS.forEach(function (b) { projectOf[b.id] = b.project || ""; });

  const SEV = {
    CRITIQUE: "var(--sev-CRITIQUE)", "ÉLEVÉE": "var(--sev-ELEVEE)",
    MOYENNE: "var(--sev-MOYENNE)", FAIBLE: "var(--sev-FAIBLE)",
  };

  // Critères notés sur 5, communs aux bugs et aux features.
  const CRIT_KEYS = ["product_importance", "be_importance", "users_impacted", "urgency", "tech_effort"];

  /* --------------------------------------------------------- Utilitaires DOM */
  function columnByProject(name) {
    return board.querySelector('.column[data-project="' + cssEsc(name) + '"]');
  }
  // Colonne fixe « non assigné » correspondant à une nature (bug / feature).
  // Il existe désormais deux colonnes sans projet, séparées par nature.
  function unassignedColumnFor(kind) {
    return board.querySelector('.column[data-fixed][data-kind="' + cssEsc(kind || "bug") + '"]');
  }
  // Un dépôt est autorisé si la colonne cible n'est pas une colonne fixe typée,
  // ou si la nature de la carte correspond à celle de la colonne fixe.
  function canDropIn(card, col) {
    if (!col) return false;
    const colKind = col.getAttribute("data-kind");
    if (col.hasAttribute("data-fixed") && colKind) {
      return (card.getAttribute("data-kind") || "bug") === colKind;
    }
    return true;
  }
  function cssEsc(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/["\\]/g, "\\$&");
  }
  function bodyOf(col) { return col.querySelector(".col-body"); }

  function updateColumn(col) {
    if (!col) return;
    const body = bodyOf(col);
    const n = body.querySelectorAll(".bcard").length;
    const count = col.querySelector(".col-count");
    if (count) count.textContent = String(n);
    const empty = body.querySelector(".col-empty");
    if (empty) empty.style.display = n ? "none" : "";
  }

  function buildCard(bug) {
    const card = document.createElement("div");
    card.className = "bcard";
    card.setAttribute("draggable", "true");
    card.setAttribute("data-id", bug.id);
    card.setAttribute("data-type", bug.type || "");
    card.setAttribute("data-kind", bug.kind || "bug");
    const kw = Array.isArray(bug.keywords) ? bug.keywords.join(" ") : "";
    card.setAttribute("data-search",
      (bug.id + " " + (bug.name || "") + " " + kw).toLowerCase());
    const crit = bug.criteria || {};
    CRIT_KEYS.forEach(function (k) {
      card.setAttribute("data-crit-" + k, crit[k] || 0);
    });
    const editUrl = (bug.kind === "feature" ? "/feature/" : "/bug/") +
      encodeURIComponent(bug.id);
    card.innerHTML =
      '<div class="bcard-top">' +
        '<span class="bid" data-kind="' + escapeHtml(bug.kind || "bug") + '">' + escapeHtml(bug.id) + "</span>" +
        '<span class="badge badge-state" data-state="' + escapeHtml(bug.state || "") + '">' +
          escapeHtml(bug.state || "") + "</span>" +
      "</div>" +
      '<div class="bcard-title">' + escapeHtml(bug.name || "Sans titre") + "</div>" +
      '<a class="icon-btn bcard-edit" href="' + editUrl + '" ' +
        'title="Éditer cet élément" aria-label="Éditer ' + escapeHtml(bug.id) + '">' +
        '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></a>';
    return card;
  }

  function updateCardState(card, state) {
    const badge = card.querySelector(".badge-state");
    if (badge) {
      badge.setAttribute("data-state", state);
      badge.textContent = state;
    }
    const id = card.getAttribute("data-id");
    const entry = BUGS.filter(function (b) { return b.id === id; })[0];
    if (entry) entry.state = state;
  }

  /* Déplace une carte (DOM) vers une colonne et persiste côté serveur. */
  async function moveCardTo(card, targetCol) {
    const sourceCol = card.closest(".column");
    const project = targetCol.getAttribute("data-project");
    const body = bodyOf(targetCol);
    body.insertBefore(card, body.querySelector(".col-empty"));
    if (sourceCol) updateColumn(sourceCol);
    updateColumn(targetCol);

    const id = card.getAttribute("data-id");
    projectOf[id] = project;
    try {
      const bug = await api("POST", "/api/bugs/" + encodeURIComponent(id) + "/move", { project: project });
      // l'état a pu changer automatiquement (BACKLOG<->TODO au passage Non assigné)
      if (bug && bug.state) updateCardState(card, bug.state);
    } catch (err) {
      showToast(err.message || "Déplacement non enregistré.", "error");
    }
  }

  /* ----------------------------------------------------- Glisser-déposer */
  let dragged = null;

  board.addEventListener("dragstart", function (e) {
    const card = e.target.closest(".bcard");
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", card.getAttribute("data-id")); } catch (x) {}
    }
  });

  board.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
    board.querySelectorAll(".col-body.drag-over").forEach(function (b) {
      b.classList.remove("drag-over");
    });
  });

  board.addEventListener("dragover", function (e) {
    const body = e.target.closest(".col-body");
    if (!body || !dragged) return;
    const col = body.closest(".column");
    // Sur une colonne fixe typée (Bugs / Feature non assignée), n'autorise le
    // dépôt que si la nature de la carte correspond : sinon on ne preventDefault
    // pas, ce qui interdit le drop et affiche le curseur « interdit ».
    if (!canDropIn(dragged, col)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
    body.classList.add("drag-over");
  });

  board.addEventListener("dragleave", function (e) {
    const body = e.target.closest(".col-body");
    if (!body) return;
    // ne retire le surlignage que si on quitte réellement la zone
    if (!body.contains(e.relatedTarget)) body.classList.remove("drag-over");
  });

  board.addEventListener("drop", function (e) {
    const body = e.target.closest(".col-body");
    if (!body || !dragged) return;
    e.preventDefault();
    body.classList.remove("drag-over");
    const targetCol = body.closest(".column");
    if (!targetCol || dragged.closest(".column") === targetCol) return;
    if (!canDropIn(dragged, targetCol)) {
      const kind = dragged.getAttribute("data-kind") === "feature" ? "Les features" : "Les bugs";
      showToast(kind + " ne peuvent pas aller dans cette colonne.", "error");
      return;
    }
    moveCardTo(dragged, targetCol);
  });

  /* -------------------------------------------------- Couche flottante (popover/menu) */
  let floating = null;
  function closeFloating() {
    if (floating && floating.parentNode) floating.parentNode.removeChild(floating);
    floating = null;
    document.removeEventListener("click", onDocClick, true);
    document.removeEventListener("keydown", onEsc, true);
  }
  function onDocClick(e) {
    if (floating && !floating.contains(e.target)) closeFloating();
  }
  function onEsc(e) {
    if (e.key === "Escape") closeFloating();
  }
  function openFloating(el, anchor) {
    closeFloating();
    floating = el;
    el.style.visibility = "hidden";
    document.body.appendChild(el);
    const r = anchor.getBoundingClientRect();
    let left = r.left + window.scrollX;
    let top = r.bottom + window.scrollY + 6;
    // garde le panneau dans la fenêtre
    const w = el.offsetWidth;
    if (left + w > window.scrollX + document.documentElement.clientWidth - 10) {
      left = r.right + window.scrollX - w;
    }
    el.style.left = Math.max(10, left) + "px";
    el.style.top = top + "px";
    el.style.visibility = "";
    // différé pour ne pas être fermé par le clic d'ouverture courant
    setTimeout(function () {
      document.addEventListener("click", onDocClick, true);
      document.addEventListener("keydown", onEsc, true);
    }, 0);
  }

  /* ------------------------------------------- Popover « ajouter un bug » */
  function openAddBugPopover(anchor, col) {
    const project = col.getAttribute("data-project");
    // Colonne fixe typée : on ne propose que les éléments de la bonne nature.
    const colKind = col.hasAttribute("data-fixed") ? col.getAttribute("data-kind") : "";
    const pop = document.createElement("div");
    pop.className = "popover";
    pop.innerHTML =
      '<div class="pop-title">Ajouter un élément à « ' +
        escapeHtml(col.querySelector(".col-name").textContent) + ' »</div>' +
      '<input type="text" placeholder="Rechercher par nom ou ID…" autocomplete="off">' +
      '<div class="pop-results"></div>';
    const input = pop.querySelector("input");
    const results = pop.querySelector(".pop-results");

    function render() {
      const q = input.value.trim().toLowerCase();
      const items = BUGS.filter(function (b) {
        if (colKind && (b.kind || "bug") !== colKind) return false; // mauvaise nature
        if ((projectOf[b.id] || "") === project) return false; // déjà dans la colonne
        if (!q) return true;
        return b.id.toLowerCase().indexOf(q) !== -1 ||
               (b.name || "").toLowerCase().indexOf(q) !== -1;
      }).slice(0, 40);

      if (!items.length) {
        results.innerHTML = '<div class="pop-empty">Aucun élément à ajouter.</div>';
        return;
      }
      results.innerHTML = "";
      items.forEach(function (b) {
        const it = document.createElement("button");
        it.type = "button";
        it.className = "pop-item";
        it.innerHTML = '<span class="pi-id">' + escapeHtml(b.id) + "</span>" +
                       '<span class="pi-name">' + escapeHtml(b.name || "Sans titre") + "</span>";
        it.addEventListener("click", function () {
          let card = board.querySelector('.bcard[data-id="' + cssEsc(b.id) + '"]');
          if (!card) {
            // sécurité : si la carte n'existe pas dans le DOM, on la fabrique
            card = buildCard(b);
            const body = bodyOf(col);
            body.insertBefore(card, body.querySelector(".col-empty"));
            updateColumn(col);
            projectOf[b.id] = project;
            api("POST", "/api/bugs/" + encodeURIComponent(b.id) + "/move", { project: project })
              .catch(function (err) { showToast(err.message, "error"); });
          } else {
            moveCardTo(card, col);
          }
          closeFloating();
          showToast(b.id + " ajouté.", "success");
        });
        results.appendChild(it);
      });
    }

    input.addEventListener("input", render);
    render();
    openFloating(pop, anchor);
    input.focus();
  }

  /* ------------------------------------------------ Menu de colonne (⋯) */
  // petits pictogrammes
  const ICO = {
    left:  '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>',
    right: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
    pencil:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
    calendar:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
    archive:'<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4"/></svg>',
    trash: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg>',
  };

  function activeCols() {
    return Array.prototype.slice.call(board.querySelectorAll(".column:not([data-fixed])"));
  }

  function persistOrder() {
    const order = activeCols().map(function (c) { return c.getAttribute("data-project"); });
    api("POST", "/api/projects/reorder", { order: order })
      .catch(function (err) { showToast(err.message, "error"); });
  }

  function moveColumn(col, dir) {
    const cols = activeCols();
    const i = cols.indexOf(col);
    const j = i + dir;
    if (j < 0 || j >= cols.length) return;
    const other = cols[j];
    if (dir < 0) board.insertBefore(col, other);   // échange avec la précédente
    else board.insertBefore(other, col);           // échange avec la suivante
    persistOrder();
  }

  async function archiveColumn(col) {
    const name = col.getAttribute("data-project");
    if (!window.confirm("Archiver le projet « " + name + " » ?\n" +
        "Il quittera le tableau et sera visible sur la page « Archives ».")) return;
    col.remove();
    try {
      await api("POST", "/api/projects/archive", { name: name });
      showToast("« " + name + " » archivé.", "success");
    } catch (err) {
      showToast(err.message || "Archivage non enregistré.", "error");
    }
  }

  function openColumnMenu(anchor, col) {
    const cols = activeCols();
    const i = cols.indexOf(col);
    const isFirst = i <= 0;
    const isLast = i >= cols.length - 1;

    function item(act, label, ico, disabled, danger) {
      return '<button type="button" data-act="' + act + '"' +
        (danger ? ' class="danger"' : "") + (disabled ? " disabled" : "") + ">" +
        ico + label + "</button>";
    }

    const menu = document.createElement("div");
    menu.className = "menu";
    menu.innerHTML =
      item("left", "Décaler à gauche", ICO.left, isFirst, false) +
      item("right", "Décaler à droite", ICO.right, isLast, false) +
      '<div class="menu-sep"></div>' +
      item("rename", "Renommer", ICO.pencil, false, false) +
      item("dates", "Dates…", ICO.calendar, false, false) +
      item("archive", "Archiver", ICO.archive, false, false) +
      '<div class="menu-sep"></div>' +
      item("delete", "Supprimer", ICO.trash, false, true);

    menu.addEventListener("click", function (e) {
      const b = e.target.closest("button[data-act]");
      if (!b || b.disabled) return;
      const act = b.getAttribute("data-act");
      const anchorBtn = anchor;
      closeFloating();
      if (act === "left") moveColumn(col, -1);
      else if (act === "right") moveColumn(col, 1);
      else if (act === "rename") startRename(col);
      else if (act === "dates") openDatesPopover(anchorBtn, col);
      else if (act === "archive") archiveColumn(col);
      else if (act === "delete") deleteColumn(col);
    });
    openFloating(menu, anchor);
  }

  /* ---------------------------------------- Dates de début / fin d'un projet */
  function fmtEu(iso) {
    if (!iso || iso.length !== 10) return "";
    return iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4);
  }

  // Met à jour la ligne de dates affichée sous le titre d'une colonne projet.
  function renderColDates(col) {
    const el = col.querySelector(".col-dates");
    if (!el) return;
    const s = fmtEu(col.getAttribute("data-start"));
    const e = fmtEu(col.getAttribute("data-end"));
    const txt = el.querySelector(".col-dates-text");
    if (!s && !e) {
      el.style.display = "none";
      if (txt) txt.textContent = "";
      return;
    }
    el.style.display = "";
    if (txt) txt.textContent = (s || "…") + " → " + (e || "…");
  }

  async function saveDates(col, start, end) {
    const name = col.getAttribute("data-project");
    if (start && end && end < start) {
      showToast("La date de fin précède la date de début.", "error");
      return;
    }
    try {
      const res = await api("POST", "/api/projects/dates",
        { name: name, start_date: start, end_date: end });
      col.setAttribute("data-start", (res && res.start_date) || "");
      col.setAttribute("data-end", (res && res.end_date) || "");
      renderColDates(col);
      showToast("Dates mises à jour.", "success");
    } catch (err) {
      showToast(err.message || "Échec de l'enregistrement.", "error");
    }
  }

  function openDatesPopover(anchor, col) {
    const name = col.getAttribute("data-project");
    const start = col.getAttribute("data-start") || "";
    const end = col.getAttribute("data-end") || "";
    const pop = document.createElement("div");
    pop.className = "popover dates-pop";
    pop.innerHTML =
      '<div class="pop-title">Dates de « ' + escapeHtml(name) + ' »</div>' +
      '<label class="dates-field">Début' +
        '<input type="date" class="d-start" value="' + escapeHtml(start) + '"></label>' +
      '<label class="dates-field">Fin' +
        '<input type="date" class="d-end" value="' + escapeHtml(end) + '"></label>' +
      '<div class="dates-actions">' +
        '<button type="button" class="btn btn-sm dates-clear">Effacer</button>' +
        '<button type="button" class="btn btn-primary btn-sm dates-save">Enregistrer</button>' +
      '</div>';
    const sEl = pop.querySelector(".d-start");
    const eEl = pop.querySelector(".d-end");
    pop.querySelector(".dates-clear").addEventListener("click", function () {
      sEl.value = ""; eEl.value = "";
    });
    pop.querySelector(".dates-save").addEventListener("click", function () {
      const s = sEl.value, e = eEl.value;
      closeFloating();
      saveDates(col, s, e);
    });
    openFloating(pop, anchor);
  }

  function startRename(col) {
    const nameEl = col.querySelector(".col-name");
    const old = col.getAttribute("data-project");
    const input = document.createElement("input");
    input.type = "text";
    input.className = "col-name-edit";
    input.value = old;
    nameEl.replaceWith(input);
    input.focus();
    input.select();

    let done = false;
    function finish(commit) {
      if (done) return;
      done = true;
      const next = input.value.trim();
      const span = document.createElement("span");
      span.className = "col-name";
      span.title = commit && next ? next : old;
      span.textContent = commit && next ? next : old;
      input.replaceWith(span);

      if (commit && next && next !== old) {
        col.setAttribute("data-project", next);
        // met à jour la table d'identité pour la recherche
        BUGS.forEach(function (b) { if (projectOf[b.id] === old) projectOf[b.id] = next; });
        api("POST", "/api/projects/rename", { old: old, new: next })
          .then(function () { showToast("Colonne renommée.", "success"); })
          .catch(function (err) { showToast(err.message, "error"); });
      }
    }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", function () { finish(true); });
  }

  async function deleteColumn(col) {
    const name = col.getAttribute("data-project");
    const cards = bodyOf(col).querySelectorAll(".bcard");
    const msg = cards.length
      ? "Supprimer la colonne « " + name + " » ?\nSes " + cards.length +
        " élément(s) repasseront en « non assigné »."
      : "Supprimer la colonne « " + name + " » ?";
    if (!window.confirm(msg)) return;

    const touched = [];
    Array.prototype.forEach.call(cards, function (card) {
      const kind = card.getAttribute("data-kind") || "bug";
      const target = unassignedColumnFor(kind);
      if (target) {
        const body = bodyOf(target);
        body.insertBefore(card, body.querySelector(".col-empty"));
        projectOf[card.getAttribute("data-id")] = "";
        if (touched.indexOf(target) === -1) touched.push(target);
      }
    });
    touched.forEach(updateColumn);
    col.remove();

    try {
      await api("POST", "/api/projects/delete", { name: name });
      showToast("Colonne supprimée.", "success");
    } catch (err) {
      showToast(err.message || "Suppression non enregistrée.", "error");
    }
  }

  /* --------------------------------------- Délégation des clics de colonne */
  board.addEventListener("click", function (e) {
    const add = e.target.closest(".col-add");
    if (add) {
      openAddBugPopover(add, add.closest(".column"));
      return;
    }
    const menu = e.target.closest(".col-menu");
    if (menu) {
      openColumnMenu(menu, menu.closest(".column"));
      return;
    }
  });

  /* ------------------------------------------------ Ajout d'une colonne */
  function buildColumn(name) {
    const col = document.createElement("section");
    col.className = "column";
    col.setAttribute("data-project", name);
    col.innerHTML =
      '<div class="col-head">' +
        '<div class="col-title">' +
          '<span class="col-name" title="' + escapeHtml(name) + '">' + escapeHtml(name) + "</span>" +
          '<span class="col-count">0</span>' +
        "</div>" +
        '<div class="col-actions">' +
          '<button type="button" class="icon-btn col-add" title="Ajouter un élément à cette colonne" aria-label="Ajouter un élément">' +
            '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></button>' +
          '<button type="button" class="icon-btn col-menu" title="Options de la colonne" aria-label="Options de la colonne">' +
            '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg></button>' +
        "</div>" +
      "</div>" +
      '<div class="col-dates" style="display:none">' +
        '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>' +
        '<span class="col-dates-text"></span>' +
      "</div>" +
      '<div class="col-body"><div class="col-empty">Déposez des éléments ici.</div></div>';
    return col;
  }

  const addColBtn = document.getElementById("add-col-btn");
  const addColForm = document.getElementById("add-col-form");
  const addColInput = document.getElementById("add-col-input");
  const addColConfirm = document.getElementById("add-col-confirm");
  const addColCancel = document.getElementById("add-col-cancel");

  function showAddColForm(show) {
    addColForm.classList.toggle("open", show);
    addColBtn.style.display = show ? "none" : "";
    if (show) { addColInput.value = ""; addColInput.focus(); }
  }

  if (addColBtn) addColBtn.addEventListener("click", function () { showAddColForm(true); });
  if (addColCancel) addColCancel.addEventListener("click", function () { showAddColForm(false); });

  async function confirmAddCol() {
    const name = addColInput.value.trim();
    if (!name) { addColInput.focus(); return; }
    if (columnByProject(name)) {
      showToast("Cette colonne existe déjà.", "error");
      return;
    }
    try {
      const res = await api("POST", "/api/projects", { name: name });
      const finalName = (res && res.name) || name;
      const col = buildColumn(finalName);
      board.insertBefore(col, addColBlock);
      showAddColForm(false);
      showToast("Colonne « " + finalName + " » créée.", "success");
    } catch (err) {
      showToast(err.message || "Création impossible.", "error");
    }
  }

  if (addColConfirm) addColConfirm.addEventListener("click", confirmAddCol);
  if (addColInput) {
    addColInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); confirmAddCol(); }
      else if (e.key === "Escape") { e.preventDefault(); showAddColForm(false); }
    });
  }

  /* ------------- Recherche à l'intérieur des colonnes fixes (non assigné) */
  // Il existe deux colonnes fixes (bugs / features) : on câble chaque champ.
  board.querySelectorAll(".col-search-input").forEach(function (colSearch) {
    const fixedCol = colSearch.closest(".column");
    const fixedBody = bodyOf(fixedCol);
    const emptyEl = fixedBody.querySelector(".col-empty");

    colSearch.addEventListener("input", function () {
      const q = colSearch.value.trim().toLowerCase();
      const cards = fixedBody.querySelectorAll(".bcard");
      let shown = 0;
      cards.forEach(function (card) {
        const hay = (card.getAttribute("data-search") || "").toLowerCase();
        const match = !q || hay.indexOf(q) !== -1;
        card.style.display = match ? "" : "none";
        if (match) shown++;
      });
      if (emptyEl) {
        if (q && shown === 0 && cards.length) {
          emptyEl.textContent = "Aucun élément ne correspond.";
          emptyEl.style.display = "";
        } else {
          emptyEl.textContent = "Déposez des éléments ici.";
          emptyEl.style.display = cards.length ? "none" : "";
        }
      }
    });
  });

  /* ---------- Réduction des colonnes fixes (indépendante l'une de l'autre) - */
  // L'état réduit / déplié est mémorisé par nature (bug / feature) afin de
  // persister entre deux visites du tableau.
  const COLLAPSE_KEY_PREFIX = "bugtrack-collapsed-";
  board.querySelectorAll(".col-collapse").forEach(function (btn) {
    const col = btn.closest(".column");
    const kind = col.getAttribute("data-kind") || "bug";
    const key = COLLAPSE_KEY_PREFIX + kind;
    let collapsed = false;
    try { collapsed = localStorage.getItem(key) === "1"; } catch (e) {}

    function apply() {
      col.classList.toggle("collapsed", collapsed);
      btn.setAttribute("aria-pressed", collapsed ? "true" : "false");
      btn.title = collapsed ? "Agrandir la colonne" : "Réduire la colonne";
    }
    apply();

    btn.addEventListener("click", function () {
      collapsed = !collapsed;
      apply();
      try { localStorage.setItem(key, collapsed ? "1" : "0"); } catch (e) {}
    });
  });

  /* ---------- Tri des colonnes fixes par critère noté sur 5 --------------- */
  board.querySelectorAll(".col-sort").forEach(function (sortBox) {
    const select = sortBox.querySelector(".col-sort-select");
    const dirBtn = sortBox.querySelector(".col-sort-dir");
    const fixedCol = sortBox.closest(".column");
    const fixedBody = bodyOf(fixedCol);
    // Ordre initial (issu du tri serveur), conservé pour l'option « ordre du tableau ».
    const originalOrder = Array.prototype.slice.call(fixedBody.querySelectorAll(".bcard"));
    let dir = "desc";

    function critVal(card, key) {
      return parseInt(card.getAttribute("data-crit-" + key), 10) || 0;
    }

    function applyColSort() {
      const key = select.value;
      const emptyEl = fixedBody.querySelector(".col-empty");
      const current = Array.prototype.slice.call(fixedBody.querySelectorAll(".bcard"));
      let ordered;
      if (!key) {
        const extra = current.filter(function (c) { return originalOrder.indexOf(c) === -1; });
        ordered = originalOrder.filter(function (c) { return fixedBody.contains(c); }).concat(extra);
      } else {
        ordered = current.sort(function (a, b) {
          const diff = critVal(b, key) - critVal(a, key);
          return dir === "desc" ? diff : -diff;
        });
      }
      ordered.forEach(function (card) { fixedBody.insertBefore(card, emptyEl); });
    }

    if (select) select.addEventListener("change", applyColSort);
    if (dirBtn) {
      dirBtn.addEventListener("click", function () {
        dir = dir === "desc" ? "asc" : "desc";
        dirBtn.textContent = dir === "desc" ? "↓" : "↑";
        dirBtn.setAttribute("data-dir", dir);
        applyColSort();
      });
    }
  });

  // initialise les compteurs / états vides
  board.querySelectorAll(".column").forEach(updateColumn);
})();
