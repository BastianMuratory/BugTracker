/* Fonctions communes à toutes les pages : notifications (toast) + appels API. */
(function () {
  "use strict";

  let toastTimer = null;

  /**
   * Affiche une notification éphémère en bas de l'écran.
   * @param {string} msg  Message à afficher.
   * @param {string} [type]  "success" | "error" | "" (neutre).
   */
  window.showToast = function (msg, type) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.className = "toast show" + (type ? " " + type : "");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      el.className = "toast";
    }, 2600);
  };

  /**
   * Petit utilitaire fetch -> JSON avec gestion d'erreur uniforme.
   * Renvoie l'objet JSON en cas de succès, lève une Error sinon.
   */
  window.api = async function (method, url, body) {
    const opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    if (!res.ok) {
      const message = (data && data.error) || "Erreur réseau (" + res.status + ")";
      throw new Error(message);
    }
    return data;
  };

  /** Échappe le HTML pour insertion sûre dans le DOM. */
  window.escapeHtml = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };
})();
