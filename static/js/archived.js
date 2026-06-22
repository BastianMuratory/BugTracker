/* Page Archives : restaurer ou supprimer un projet archivé. */
(function () {
  "use strict";

  const list = document.querySelector(".arch-list");
  if (!list) return;

  function countLeft() {
    return list.querySelectorAll(".arch-project").length;
  }

  function refreshHeader() {
    const sub = document.querySelector(".page-head .sub");
    const n = countLeft();
    if (sub) sub.textContent = n + " projet" + (n !== 1 ? "s" : "") + " archivé" + (n !== 1 ? "s" : "");
    if (n === 0) {
      const main = document.querySelector(".main");
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.innerHTML = 'Aucun projet archivé pour l\'instant.<br>' +
        'Archivez un projet terminé depuis le <a href="/board">Tableau</a>.';
      list.replaceWith(empty);
    }
  }

  list.addEventListener("click", async function (e) {
    const section = e.target.closest(".arch-project");
    if (!section) return;
    const name = section.getAttribute("data-project");

    if (e.target.closest(".arch-restore")) {
      try {
        await api("POST", "/api/projects/restore", { name: name });
        section.remove();
        refreshHeader();
        showToast("« " + name + " » restauré sur le tableau.", "success");
      } catch (err) {
        showToast(err.message || "Restauration impossible.", "error");
      }
      return;
    }

    if (e.target.closest(".arch-delete")) {
      const n = section.querySelectorAll(".bcard").length;
      const msg = n
        ? "Supprimer définitivement le projet « " + name + " » ?\nSes " + n +
          " bug(s) repasseront en « Non assigné »."
        : "Supprimer définitivement le projet « " + name + " » ?";
      if (!window.confirm(msg)) return;
      try {
        await api("POST", "/api/projects/delete", { name: name });
        section.remove();
        refreshHeader();
        showToast("Projet « " + name + " » supprimé.", "success");
      } catch (err) {
        showToast(err.message || "Suppression impossible.", "error");
      }
    }
  });
})();
