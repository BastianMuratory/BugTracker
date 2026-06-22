/* Page d'édition / création d'un élément (bug OU feature).
   Le même script sert les deux formulaires : la configuration (base d'API,
   libellés, URL de liste) est lue sur les data-attributes du <form>, et les
   champs sont collectés génériquement via [data-field]. */
(function () {
  "use strict";

  const form = document.getElementById("item-form");
  if (!form) return;

  // Configuration portée par le formulaire (cf. edit.html / feature_edit.html).
  const API_BASE = form.dataset.apiBase || "/api/bugs";   // ex. "/api/features"
  const ENTITY = form.dataset.entity || "bug";            // "bug" | "feature"
  const ENTITY_LABEL = ENTITY === "feature" ? "la feature" : "le bug";
  const LIST_URL = form.dataset.listUrl || "/bugs";
  const PATH_BASE = form.dataset.pathBase || "/bug";      // ex. "/feature"

  const nameInput = document.getElementById("f-name");
  const kwInput = document.getElementById("kw-input");
  const kwField = document.getElementById("kw-field");
  const occBody = document.getElementById("occ-body");
  const occEmpty = document.getElementById("occ-empty");
  const occCount = document.getElementById("occ-count");
  const occTemplate = document.getElementById("occ-template");
  const addOccBtn = document.getElementById("add-occ");
  const saveBtn = document.getElementById("save-btn");
  const editActions = form.querySelector(".edit-actions");

  const imgDropzone = document.getElementById("img-dropzone");
  const imgGallery = document.getElementById("img-gallery");
  const imgEmpty = document.getElementById("img-empty");
  const imgCount = document.getElementById("img-count");
  const imgFileInput = document.getElementById("img-file-input");
  const addImgBtn = document.getElementById("add-img-btn");
  const unsavedBanner = document.getElementById("unsaved-banner");

  /* ----------------------------------------------- Bandeau « non enregistré » */
  // Tout changement dans le formulaire (champ texte, sélecteur, note, mot-clé,
  // occurrence…) affiche le bandeau ; il disparaît une fois l'enregistrement réussi.
  let dirty = false;
  function markDirty() {
    if (dirty) return;
    dirty = true;
    if (unsavedBanner) unsavedBanner.classList.add("show");
    document.body.classList.add("has-unsaved");
  }
  function clearDirty() {
    dirty = false;
    if (unsavedBanner) unsavedBanner.classList.remove("show");
    document.body.classList.remove("has-unsaved");
  }
  form.addEventListener("input", markDirty);
  form.addEventListener("change", markDirty);
  form.addEventListener("click", function (e) {
    if (e.target.closest(".rating button, .row-del, #add-occ, .chip button")) markDirty();
  });

  const TEXT_FIELDS = [
    "description", "nas_link", "observed_behavior", "expected_behavior", "conditions", "frequency",
  ];
  const OCC_FIELDS = ["location", "person", "system", "date"];
  // NB : la collecte du payload est désormais générique (tous les [data-field]),
  // ce qui permet à ce même script de gérer les champs propres aux features.

  /* ------------------------------------- Sélecteurs colorés (état / type) */
  // La case prend la couleur du badge correspondant ; on la met à jour à chaque
  // changement (l'état initial est rendu côté serveur via data-value).
  Array.prototype.forEach.call(form.querySelectorAll(".swatch-select"), function (sel) {
    sel.addEventListener("change", function () { sel.dataset.value = sel.value; });
  });

  /* ---------------------------------------------- Critères (note de 1 à 5) */
  // Chaque critère est un groupe de 5 boutons cumulatifs (façon « étoiles »).
  // La note retenue est stockée dans un input caché [data-criterion] ; re-cliquer
  // sur la note active la remet à 0 (« non noté »).
  Array.prototype.forEach.call(form.querySelectorAll(".rating"), function (rating) {
    const hidden = rating.querySelector("input[data-criterion]");
    const buttons = rating.querySelectorAll("button[data-val]");
    if (!hidden) return;

    function paint(val) {
      Array.prototype.forEach.call(buttons, function (btn) {
        const n = parseInt(btn.getAttribute("data-val"), 10);
        const on = val > 0 && val >= n;
        btn.classList.toggle("on", on);
        btn.setAttribute("aria-pressed", val === n ? "true" : "false");
      });
      rating.classList.toggle("is-set", val > 0);
    }
    function setVal(val) { hidden.value = String(val); paint(val); }

    rating.addEventListener("click", function (e) {
      const btn = e.target.closest("button[data-val]");
      if (!btn) return;
      const n = parseInt(btn.getAttribute("data-val"), 10);
      const cur = parseInt(hidden.value, 10) || 0;
      setVal(cur === n ? 0 : n); // re-cliquer la note active l'efface
    });

    paint(parseInt(hidden.value, 10) || 0);
  });

  /* ----------------------- Création : état par défaut selon le projet choisi */
  // À la création, l'état par défaut est BACKLOG ; dès qu'un projet est associé,
  // un bug encore en BACKLOG passe en TODO (et inversement si on retire le projet).
  // On ne force rien si l'utilisateur a choisi WIP/DONE, ni une fois le bug créé.
  (function () {
    const projectField = form.querySelector('[data-field="project"]');
    const stateField = form.querySelector('[data-field="state"]');
    if (!projectField || !stateField) return;
    function syncStateFromProject() {
      if (form.dataset.isNew !== "1") return; // règle limitée à la création
      const hasProject = projectField.value.trim() !== "";
      if (hasProject && stateField.value === "BACKLOG") stateField.value = "TODO";
      else if (!hasProject && stateField.value === "TODO") stateField.value = "BACKLOG";
      stateField.dataset.value = stateField.value; // resynchronise la couleur de la case
    }
    projectField.addEventListener("input", syncStateFromProject);
    projectField.addEventListener("change", syncStateFromProject);
  })();

  /* ------------------------------------------------------------- Mots-clés */
  function existingKeywords() {
    return Array.prototype.map.call(
      kwInput.querySelectorAll(".chip"),
      function (c) { return (c.getAttribute("data-value") || "").toLowerCase(); }
    );
  }

  function addKeyword(raw) {
    const value = (raw || "").trim();
    if (!value) return;
    if (existingKeywords().indexOf(value.toLowerCase()) !== -1) return; // pas de doublon
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.setAttribute("data-value", value);
    chip.innerHTML = escapeHtml(value) +
      '<button type="button" aria-label="Retirer le mot-clé">\u00d7</button>';
    kwInput.insertBefore(chip, kwField);
  }

  if (kwField) {
    kwField.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === ",") {
        e.preventDefault();
        addKeyword(kwField.value);
        kwField.value = "";
      } else if (e.key === "Backspace" && kwField.value === "") {
        const chips = kwInput.querySelectorAll(".chip");
        if (chips.length) chips[chips.length - 1].remove();
      }
    });
    kwField.addEventListener("blur", function () {
      addKeyword(kwField.value);
      kwField.value = "";
    });
  }

  if (kwInput) {
    kwInput.addEventListener("click", function (e) {
      const btn = e.target.closest(".chip button");
      if (btn) {
        btn.closest(".chip").remove();
        return;
      }
      if (e.target === kwInput) kwField.focus();
    });
  }

  /* ---------------------------------------------------------- Occurrences */
  function refreshOccState() {
    const rows = occBody.querySelectorAll(".occ-row");
    occCount.textContent = String(rows.length);
    occEmpty.style.display = rows.length ? "none" : "";
  }

  function addOccurrence(focus) {
    const frag = occTemplate.content.cloneNode(true);
    // insère la nouvelle ligne juste avant la ligne "aucune occurrence"
    occBody.insertBefore(frag, occEmpty);
    refreshOccState();
    if (focus) {
      const rows = occBody.querySelectorAll(".occ-row");
      const last = rows[rows.length - 1];
      const first = last && last.querySelector("input");
      if (first) first.focus();
    }
  }

  if (addOccBtn) addOccBtn.addEventListener("click", function () { addOccurrence(true); });

  if (occBody) {
    occBody.addEventListener("click", function (e) {
      const del = e.target.closest(".row-del");
      if (del) {
        del.closest(".occ-row").remove();
        refreshOccState();
      }
    });
  }

  /* ------------------------------------------------- Dates (format européen) */
  // Les champs date sont saisis au format JJ/MM/AAAA puis stockés en ISO.
  function maskDate(el) {
    const digits = el.value.replace(/\D/g, "").slice(0, 8);
    let out = digits.slice(0, 2);
    if (digits.length >= 3) out += "/" + digits.slice(2, 4);
    if (digits.length >= 5) out += "/" + digits.slice(4, 8);
    el.value = out;
  }

  function euToIso(s) {
    s = (s || "").trim();
    const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(s);
    if (!m) return s; // vide ou format inattendu : on garde tel quel
    const d = parseInt(m[1], 10), mo = parseInt(m[2], 10);
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return s; // date invalide : inchangée
    return m[3] + "-" + m[2] + "-" + m[1];
  }

  // Formate un horodatage ISO en JJ/MM/AAAA HH:MM (en UTC, comme le filtre serveur).
  function isoToEuDateTime(iso) {
    const dt = new Date(iso);
    if (isNaN(dt.getTime())) return iso;
    const p = function (n) { return (n < 10 ? "0" : "") + n; };
    return p(dt.getUTCDate()) + "/" + p(dt.getUTCMonth() + 1) + "/" + dt.getUTCFullYear() +
           " " + p(dt.getUTCHours()) + ":" + p(dt.getUTCMinutes());
  }

  if (occBody) {
    occBody.addEventListener("input", function (e) {
      if (e.target.classList && e.target.classList.contains("date-eu")) maskDate(e.target);
    });
  }

  /* ------------------------------------------------------------- Images */
  function refreshImgState() {
    const thumbs = imgGallery.querySelectorAll(".img-thumb");
    if (imgCount) imgCount.textContent = String(thumbs.length);
    if (imgEmpty) imgEmpty.style.display = thumbs.length ? "none" : "";
  }

  function addThumb(filename, url) {
    const fig = document.createElement("figure");
    fig.className = "img-thumb";
    fig.setAttribute("data-filename", filename);
    fig.innerHTML =
      '<img src="' + escapeHtml(url) + '" alt="Image jointe au bug" loading="lazy">' +
      '<button type="button" class="icon-btn img-del" title="Retirer cette image" aria-label="Retirer cette image">' +
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
      '<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg></button>';
    imgGallery.appendChild(fig);
    refreshImgState();
  }

  async function uploadFiles(fileList) {
    const files = Array.prototype.filter.call(fileList || [], function (f) {
      return f && f.type && f.type.indexOf("image/") === 0;
    });
    if (!files.length) return;

    // L'élément doit exister côté serveur avant qu'on puisse lui rattacher une
    // image : on enregistre d'abord si on est encore en mode création.
    if (form.dataset.isNew === "1") {
      if (!nameInput.value.trim()) {
        showToast("Donnez un titre avant d'ajouter une image.", "error");
        nameInput.focus();
        return;
      }
      if (saving) {
        showToast("Enregistrement en cours, réessayez dans un instant.", "error");
        return;
      }
      await save({ redirectAfter: false });
      if (form.dataset.isNew === "1") return; // la sauvegarde a échoué
    }

    const fd = new FormData();
    files.forEach(function (f) { fd.append("files", f, f.name || "image.png"); });

    try {
      const res = await fetch(API_BASE + "/" + encodeURIComponent(form.dataset.bugId) + "/images", {
        method: "POST",
        body: fd,
      });
      const data = await res.json().catch(function () { return null; });
      if (!res.ok) throw new Error((data && data.error) || "Échec de l'envoi.");
      imgGallery.innerHTML = "";
      (data.images || []).forEach(function (fname) {
        addThumb(fname, "/uploads/" + encodeURIComponent(fname));
      });
      refreshImgState();
      showToast(files.length > 1 ? "Images ajoutées." : "Image ajoutée.", "success");
    } catch (err) {
      showToast(err.message || "Échec de l'envoi de l'image.", "error");
    }
  }

  if (addImgBtn) addImgBtn.addEventListener("click", function () { imgFileInput.click(); });
  if (imgFileInput) {
    imgFileInput.addEventListener("change", function () {
      uploadFiles(imgFileInput.files);
      imgFileInput.value = "";
    });
  }

  if (imgDropzone) {
    imgDropzone.addEventListener("dragover", function (e) {
      e.preventDefault();
      imgDropzone.classList.add("drag-over");
    });
    imgDropzone.addEventListener("dragleave", function (e) {
      if (!imgDropzone.contains(e.relatedTarget)) imgDropzone.classList.remove("drag-over");
    });
    imgDropzone.addEventListener("drop", function (e) {
      e.preventDefault();
      imgDropzone.classList.remove("drag-over");
      if (e.dataTransfer && e.dataTransfer.files) uploadFiles(e.dataTransfer.files);
    });
  }

  // Coller une capture d'écran (Ctrl+V) n'importe où sur le formulaire.
  form.addEventListener("paste", function (e) {
    const items = (e.clipboardData && e.clipboardData.files) || [];
    if (items.length) {
      e.preventDefault();
      uploadFiles(items);
    }
  });

  if (imgGallery) {
    imgGallery.addEventListener("click", async function (e) {
      const del = e.target.closest(".img-del");
      if (del) {
        const fig = del.closest(".img-thumb");
        const filename = fig.getAttribute("data-filename");
        if (!window.confirm("Retirer cette image ?")) return;
        try {
          await api("DELETE", API_BASE + "/" + encodeURIComponent(form.dataset.bugId) +
            "/images/" + encodeURIComponent(filename));
          fig.remove();
          refreshImgState();
          showToast("Image retirée.", "success");
        } catch (err) {
          showToast(err.message || "Échec de la suppression.", "error");
        }
        return;
      }
      const img = e.target.closest(".img-thumb img");
      if (img) openLightbox(img.src);
    });
  }

  function openLightbox(src) {
    const box = document.createElement("div");
    box.className = "img-lightbox";
    box.innerHTML = '<img src="' + escapeHtml(src) + '" alt="">';
    function close() {
      box.remove();
      document.removeEventListener("keydown", onKey);
    }
    function onKey(e) { if (e.key === "Escape") close(); }
    box.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    document.body.appendChild(box);
  }

  /* -------------------------------------------------------- Collecte data */
  function gatherPayload() {
    const payload = {
      name: nameInput.value.trim(),
      keywords: Array.prototype.map.call(
        kwInput.querySelectorAll(".chip"),
        function (c) { return c.getAttribute("data-value"); }
      ),
      occurrences: [],
    };

    // Collecte générique : tous les champs marqués [data-field] hors du tableau
    // d'occurrences (qui est traité séparément ci-dessous). Cela couvre l'état,
    // la criticité/priorité (type), le projet, le responsable et tous les champs
    // texte, qu'ils soient propres aux bugs ou aux features.
    Array.prototype.forEach.call(form.querySelectorAll("[data-field]"), function (el) {
      if (el.closest("#occ-body")) return;
      payload[el.getAttribute("data-field")] = el.value;
    });

    Array.prototype.forEach.call(occBody.querySelectorAll(".occ-row"), function (row) {
      const occ = {};
      const id = row.getAttribute("data-occ-id");
      if (id) occ.id = id;
      OCC_FIELDS.forEach(function (f) {
        const inp = row.querySelector('[data-field="' + f + '"]');
        let val = inp ? inp.value.trim() : "";
        if (f === "date") val = euToIso(val); // JJ/MM/AAAA -> AAAA-MM-JJ
        occ[f] = val;
      });
      // ignore les lignes entièrement vides
      if (OCC_FIELDS.some(function (f) { return occ[f]; })) payload.occurrences.push(occ);
    });

    // Critères d'évaluation (note 1-5 ; 0 = non noté). Chaque note est portée par
    // un input caché [data-criterion]. Le serveur valide les bornes et ignore les
    // clés inconnues, mais on normalise déjà ici.
    const criteria = {};
    Array.prototype.forEach.call(form.querySelectorAll("[data-criterion]"), function (el) {
      const v = parseInt(el.value, 10);
      criteria[el.getAttribute("data-criterion")] = (v >= 1 && v <= 5) ? v : 0;
    });
    payload.criteria = criteria;

    return payload;
  }

  /* Réinjecte l'état renvoyé par le serveur (ids d'occurrences, date de MAJ). */
  function applyServerState(bug) {
    if (!bug) return;
    const rows = occBody.querySelectorAll(".occ-row");
    (bug.occurrences || []).forEach(function (occ, i) {
      if (rows[i] && occ.id) rows[i].setAttribute("data-occ-id", occ.id);
    });
    const modified = Array.prototype.filter.call(
      form.querySelectorAll(".edit-sub .muted"),
      function (s) { return s.textContent.indexOf("Modifié") === 0; }
    )[0];
    if (modified && bug.updated_at) {
      modified.textContent = "Modifié\u00a0: " + isoToEuDateTime(bug.updated_at);
    }
  }

  /* ----------------------------------------------- Bascule création -> édition */
  function switchToEditMode(bug) {
    form.dataset.isNew = "0";
    form.dataset.bugId = bug.id;

    const bid = form.querySelector(".bid");
    if (bid) bid.textContent = bug.id;
    document.title = bug.id + " — " + (bug.name || "Sans titre") + " — Bugtrack";

    // URL -> /bug/<id> ou /feature/<id> sans recharger la page
    try {
      history.replaceState({}, "", PATH_BASE + "/" + encodeURIComponent(bug.id));
    } catch (e) { /* environnements sans History API */ }

    // ajoute un bouton "Supprimer" (absent en mode création)
    if (editActions && !document.getElementById("delete-btn")) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn btn-danger";
      del.id = "delete-btn";
      del.textContent = "Supprimer";
      del.addEventListener("click", onDelete);
      saveBtn.insertAdjacentElement("afterend", del);
    }
  }

  /* ----------------------------------------------------------------- Save */
  // Par défaut, un enregistrement réussi renvoie vers la page de liste (Bugs /
  // Features). On désactive ce renvoi pour l'enregistrement automatique déclenché
  // en coulisse avant l'envoi d'une image (l'utilisateur n'a pas cliqué "Enregistrer").
  let saving = false;
  async function save(opts) {
    const redirectAfter = !opts || opts.redirectAfter !== false;
    if (saving) return;
    if (!nameInput.value.trim()) {
      showToast("Le titre est obligatoire.", "error");
      nameInput.focus();
      return;
    }
    saving = true;
    saveBtn.disabled = true;
    const wasNew = form.dataset.isNew === "1";
    const payload = gatherPayload();

    try {
      let bug;
      if (wasNew) {
        bug = await api("POST", API_BASE, payload);
        switchToEditMode(bug);
        showToast(bug.id + " créé.", "success");
      } else {
        bug = await api("PUT", API_BASE + "/" + encodeURIComponent(form.dataset.bugId), payload);
        showToast("Modifications enregistrées.", "success");
      }
      applyServerState(bug);
      clearDirty();
      if (redirectAfter) {
        window.location.href = LIST_URL;
        return;
      }
    } catch (err) {
      showToast(err.message || "Échec de l'enregistrement.", "error");
    } finally {
      saving = false;
      saveBtn.disabled = false;
    }
  }

  /* --------------------------------------------------------------- Delete */
  async function onDelete() {
    const id = form.dataset.bugId;
    if (!id) return;
    if (!window.confirm("Supprimer définitivement " + ENTITY_LABEL + " " + id + " ?")) return;
    try {
      await api("DELETE", API_BASE + "/" + encodeURIComponent(id));
      window.location.href = LIST_URL;
    } catch (err) {
      showToast(err.message || "Échec de la suppression.", "error");
    }
  }

  /* ----------------------------------------------------------- Évènements */
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    save();
  });

  const initialDelete = document.getElementById("delete-btn");
  if (initialDelete) initialDelete.addEventListener("click", onDelete);

  // Ctrl/Cmd + S = enregistrer
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      save();
    }
  });

  refreshOccState();
  refreshImgState();
})();
