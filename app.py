"""
Suivi de bugs — application Flask.

Pages :
  GET  /                -> redirige vers la liste des bugs
  GET  /bugs            -> liste des bugs (grille de cartes + recherche/filtres)
  GET  /features        -> liste des features (grille de cartes + recherche/filtres)
  GET  /new             -> page de choix « bug ou feature ? »
  GET  /bug/new         -> formulaire de création d'un bug
  GET  /bug/<id>        -> formulaire d'édition d'un bug
  GET  /feature/new     -> formulaire de création d'une feature
  GET  /feature/<id>    -> formulaire d'édition d'une feature
  GET  /board           -> tableau type Kanban (bugs ET features mélangés)
  GET  /archived        -> projets archivés (terminés) et leurs éléments
  GET  /login           -> page de connexion
  POST /login           -> traitement de la connexion
  POST /logout          -> déconnexion

API JSON :
  GET    /api/bugs                 -> liste des bugs
  GET    /api/features             -> liste des features
  POST   /api/bugs                 -> crée un bug
  POST   /api/features             -> crée une feature
  PUT    /api/(bugs|features)/<id>            -> met à jour un élément
  DELETE /api/(bugs|features)/<id>            -> supprime un élément
  POST   /api/(bugs|features)/<id>/move       -> déplace un élément vers un projet
  POST   /api/(bugs|features)/<id>/images        -> ajoute des images à un élément
  DELETE /api/(bugs|features)/<id>/images/<file> -> retire une image d'un élément
  POST   /api/projects         -> crée une colonne/projet
  POST   /api/projects/rename  -> renomme une colonne/projet
  POST   /api/projects/dates   -> définit les dates de début/fin d'un projet
  POST   /api/projects/delete  -> supprime une colonne/projet
  POST   /api/projects/archive -> archive un projet (le retire du tableau)
  POST   /api/projects/restore -> désarchive un projet
  POST   /api/projects/reorder -> réordonne les colonnes
  GET    /api/export           -> télécharge data/bugs.json (sauvegarde)
  GET    /uploads/<file>       -> sert une image jointe à un élément

Authentification :
  Toute l'application (pages ET API) est protégée par connexion obligatoire
  (voir require_login() ci-dessous). Il n'y a pas de page d'inscription : les
  comptes sont créés en local par l'administrateur via `manage_users.py`
  (voir le README). Les mots de passe sont hashés (werkzeug.security) et
  stockés dans data/users.json, jamais en clair.
"""
import os
import secrets
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for, abort, send_file,
    session, send_from_directory,
)

import auth
import database as db

app = Flask(__name__)

# Taille max d'une requête (upload d'images) : 16 Mo. Évite qu'un envoi
# malencontreux (ou abusif) ne sature le disque du Raspberry Pi.
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

# Clé secrète pour signer les cookies de session. En production, définissez
# la variable d'environnement BUGTRACK_SECRET_KEY pour qu'elle reste stable
# entre redémarrages (sinon tous les utilisateurs sont déconnectés à chaque
# redémarrage du serveur).
app.config["SECRET_KEY"] = os.environ.get("BUGTRACK_SECRET_KEY") or secrets.token_hex(32)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# Cookie de session : non accessible en JavaScript, pas envoyé en requête
# cross-site (réduit les risques XSS/CSRF sur le cookie). Si l'application
# est servie en HTTPS (recommandé, même via un reverse proxy local),
# décommentez la ligne SESSION_COOKIE_SECURE.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# app.config["SESSION_COOKIE_SECURE"] = True

# Routes accessibles SANS être connecté : la page de connexion elle-même et
# les fichiers statiques (CSS/JS), sinon /login ne pourrait pas s'afficher.
PUBLIC_ENDPOINTS = {"login", "login_post", "static"}


# Ordre d'importance pour le tri des listes (du plus au moins important).
# Même échelle pour la criticité des bugs et la priorité des features.
TYPE_PRIORITY = ["CRITIQUE", "ÉLEVÉE", "MOYENNE", "FAIBLE"]


def _type_rank(bug):
    t = bug.get("type", "")
    return TYPE_PRIORITY.index(t) if t in TYPE_PRIORITY else len(TYPE_PRIORITY)


# --------------------------------------------------------------------------- #
# Authentification
# --------------------------------------------------------------------------- #
@app.before_request
def require_login():
    """Bloque toute requête (pages ET API) si personne n'est connecté."""
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if not session.get("user"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentification requise."}), 401
        return redirect(url_for("login", next=request.path))
    return None


@app.get("/login")
def login():
    if session.get("user"):
        return redirect(url_for("bugs_page"))
    return render_template("login.html", error=None)


@app.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    user = auth.verify_login(username, password)
    if not user:
        return render_template("login.html", error="Nom d'utilisateur ou mot de passe incorrect."), 401
    session.clear()
    session["user"] = user["username"]
    session.permanent = True

    next_url = request.args.get("next") or url_for("bugs_page")
    # Sécurité minimale contre les redirections ouvertes : on n'autorise que
    # les chemins internes (qui commencent par "/").
    if not next_url.startswith("/"):
        next_url = url_for("bugs_page")
    return redirect(next_url)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.template_filter("eurodate")
def eurodate(value, with_time=True):
    """Formate une date ISO en format européen : 18/06/2026 (et HH:MM si demandé)."""
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return value
    return dt.strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")


@app.template_filter("eudate")
def eudate(value):
    """Affiche une date ISO (YYYY-MM-DD) au format européen DD/MM/YYYY.
    Toute autre valeur est renvoyée telle quelle."""
    v = (value or "").strip()
    if len(v) == 10 and v[4] == "-" and v[7] == "-":
        y, m, d = v[0:4], v[5:7], v[8:10]
        if y.isdigit() and m.isdigit() and d.isdigit():
            return "{}/{}/{}".format(d, m, y)
    return v


def _payload():
    return request.get_json(silent=True) or {}


def _sort_bugs(bugs):
    # Ordre stable et lisible : par identifiant.
    return sorted(bugs, key=lambda b: b.get("id", ""))


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return redirect(url_for("bugs_page"))


@app.get("/bugs")
def bugs_page():
    # Tri principal : criticité (CRITIQUE > ÉLEVÉE > MOYENNE > FAIBLE).
    # Tri secondaire : les plus récemment modifiés d'abord (tri stable en 2 passes).
    bugs = sorted(db.list_bugs(), key=lambda b: b.get("updated_at", ""), reverse=True)
    bugs = sorted(bugs, key=_type_rank)
    return render_template(
        "bugs.html",
        bugs=bugs,
        states=db.VALID_STATES,
        types=db.VALID_TYPES,
    )


@app.get("/features")
def features_page():
    # Tri principal : priorité (CRITIQUE > ÉLEVÉE > MOYENNE > FAIBLE).
    # Tri secondaire : les plus récemment modifiées d'abord.
    features = sorted(db.list_features(), key=lambda b: b.get("updated_at", ""), reverse=True)
    features = sorted(features, key=_type_rank)
    return render_template(
        "features.html",
        features=features,
        states=db.VALID_STATES,
        types=db.VALID_TYPES,
    )


@app.get("/new")
def new_chooser():
    """Page intermédiaire : « Quel type de point voulez-vous ajouter ? »."""
    return render_template("new.html")


@app.get("/bug/new")
def bug_new():
    return render_template(
        "edit.html",
        bug=None,
        is_new=True,
        states=db.VALID_STATES,
        types=db.VALID_TYPES,
        projects=db.list_projects(),
    )


@app.get("/bug/<bug_id>")
def bug_edit(bug_id):
    bug = db.get_bug(bug_id)
    if not bug:
        abort(404)
    return render_template(
        "edit.html",
        bug=bug,
        is_new=False,
        states=db.VALID_STATES,
        types=db.VALID_TYPES,
        projects=db.list_projects(),
    )


@app.get("/feature/new")
def feature_new():
    return render_template(
        "feature_edit.html",
        feature=None,
        is_new=True,
        states=db.VALID_STATES,
        types=db.VALID_TYPES,
        projects=db.list_projects(),
    )


@app.get("/feature/<feature_id>")
def feature_edit(feature_id):
    feature = db.get_item(feature_id)
    if not feature:
        abort(404)
    return render_template(
        "feature_edit.html",
        feature=feature,
        is_new=False,
        states=db.VALID_STATES,
        types=db.VALID_TYPES,
        projects=db.list_projects(),
    )


@app.get("/board")
def board_page():
    bugs = db.list_all_items()
    projects = db.list_projects()
    meta = db.get_project_meta()

    columns = []
    # Deux colonnes fixes pour les éléments sans projet, séparées par nature :
    # les bugs d'un côté, les features de l'autre. Elles servent aussi de
    # source / cible de glisser-déposer (la nature de la carte doit correspondre
    # à la colonne fixe ciblée).
    unassigned = [b for b in bugs if not (b.get("project") or "").strip()]
    unassigned_bugs = [b for b in unassigned if b.get("kind", "bug") != "feature"]
    unassigned_features = [b for b in unassigned if b.get("kind") == "feature"]
    columns.append({"name": "", "label": "Bugs non assignée", "fixed": True,
                    "kind": "bug", "bugs": _sort_bugs(unassigned_bugs)})
    columns.append({"name": "", "label": "Feature non assignée", "fixed": True,
                    "kind": "feature", "bugs": _sort_bugs(unassigned_features)})
    for p in projects:
        col_bugs = [b for b in bugs if (b.get("project") or "").strip() == p]
        pm = meta.get(p, {})
        columns.append({"name": p, "label": p, "fixed": False, "kind": "",
                        "start_date": pm.get("start_date", ""),
                        "end_date": pm.get("end_date", ""),
                        "bugs": _sort_bugs(col_bugs)})

    # Index léger pour la recherche "ajouter un point" + filtre de colonne.
    bug_index = [
        {
            "id": b["id"],
            "kind": b.get("kind", "bug"),
            "name": b.get("name", ""),
            "state": b.get("state", "BACKLOG"),
            "type": b.get("type", db.DEFAULT_TYPE),
            "project": (b.get("project") or "").strip(),
            "keywords": b.get("keywords", []),
        }
        for b in bugs
    ]
    return render_template(
        "board.html",
        columns=columns,
        bug_index=bug_index,
    )


@app.get("/archived")
def archived_page():
    bugs = db.list_all_items()
    archived = db.list_archived_projects()
    projects = []
    for name in archived:
        col_bugs = _sort_bugs(
            [b for b in bugs if (b.get("project") or "").strip() == name]
        )
        projects.append({"name": name, "bugs": col_bugs})
    return render_template("archived.html", projects=projects)


# --------------------------------------------------------------------------- #
# API — bugs & features
#
# La création diffère (champs différents) : deux endpoints distincts.
# La mise à jour / suppression / déplacement / images se font par identifiant et
# sont indifférents à la nature : une même vue est exposée sous /api/bugs/<id>
# ET /api/features/<id> (plusieurs règles, un seul gestionnaire).
# --------------------------------------------------------------------------- #
@app.get("/api/bugs")
def api_list_bugs():
    return jsonify(db.list_bugs())


@app.get("/api/features")
def api_list_features():
    return jsonify(db.list_features())


@app.post("/api/bugs")
def api_create_bug():
    return jsonify(db.create_bug(_payload())), 201


@app.post("/api/features")
def api_create_feature():
    return jsonify(db.create_feature(_payload())), 201


@app.put("/api/bugs/<item_id>")
@app.put("/api/features/<item_id>")
def api_update_item(item_id):
    item = db.update_item(item_id, _payload())
    if not item:
        return jsonify({"error": "Élément introuvable"}), 404
    return jsonify(item)


@app.delete("/api/bugs/<item_id>")
@app.delete("/api/features/<item_id>")
def api_delete_item(item_id):
    if not db.delete_item(item_id):
        return jsonify({"error": "Élément introuvable"}), 404
    return jsonify({"ok": True})


@app.post("/api/bugs/<item_id>/move")
@app.post("/api/features/<item_id>/move")
def api_move_item(item_id):
    item = db.set_bug_project(item_id, _payload().get("project", ""))
    if not item:
        return jsonify({"error": "Élément introuvable"}), 404
    return jsonify(item)


@app.post("/api/bugs/<item_id>/images")
@app.post("/api/features/<item_id>/images")
def api_upload_item_images(item_id):
    """Reçoit une ou plusieurs images (multipart/form-data, champ "files") et
    les rattache à l'élément. Accepte aussi un seul fichier sous le champ "file"
    (cas du copier-coller d'une seule capture d'écran)."""
    if not db.get_item(item_id):
        return jsonify({"error": "Élément introuvable"}), 404

    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "Aucun fichier reçu."}), 400

    saved = []
    item = None
    for f in files:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        mimetype = (f.mimetype or "")
        if ext not in db.IMAGE_EXTENSIONS and not mimetype.startswith("image/"):
            return jsonify({"error": "Format non supporté : " + (f.filename or "")}), 400
        fname = db.save_image(f, f.filename)
        item = db.add_bug_image(item_id, fname)
        saved.append(fname)

    if not saved:
        return jsonify({"error": "Aucune image valide reçue."}), 400
    return jsonify(item), 201


@app.delete("/api/bugs/<item_id>/images/<filename>")
@app.delete("/api/features/<item_id>/images/<filename>")
def api_delete_item_image(item_id, filename):
    item = db.remove_bug_image(item_id, filename)
    if not item:
        return jsonify({"error": "Image introuvable sur cet élément"}), 404
    return jsonify(item)


@app.get("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(db.UPLOADS_DIR, filename)


# --------------------------------------------------------------------------- #
# API — projets / colonnes
# --------------------------------------------------------------------------- #
@app.post("/api/projects")
def api_create_project():
    name = db.create_project(_payload().get("name", ""))
    if not name:
        return jsonify({"error": "Nom de projet invalide"}), 400
    return jsonify({"name": name}), 201


@app.post("/api/projects/rename")
def api_rename_project():
    p = _payload()
    if not db.rename_project(p.get("old", ""), p.get("new", "")):
        return jsonify({"error": "Renommage impossible"}), 400
    return jsonify({"ok": True})


@app.post("/api/projects/dates")
def api_project_dates():
    p = _payload()
    res = db.set_project_dates(
        p.get("name", ""), p.get("start_date", ""), p.get("end_date", "")
    )
    if not res:
        return jsonify({"error": "Projet invalide"}), 400
    return jsonify(res)


@app.post("/api/projects/delete")
def api_delete_project():
    db.delete_project(_payload().get("name", ""))
    return jsonify({"ok": True})


@app.post("/api/projects/archive")
def api_archive_project():
    if not db.archive_project(_payload().get("name", "")):
        return jsonify({"error": "Archivage impossible"}), 400
    return jsonify({"ok": True})


@app.post("/api/projects/restore")
def api_restore_project():
    if not db.restore_project(_payload().get("name", "")):
        return jsonify({"error": "Restauration impossible"}), 400
    return jsonify({"ok": True})


@app.post("/api/projects/reorder")
def api_reorder_projects():
    return jsonify({"order": db.reorder_projects(_payload().get("order", []))})


# --------------------------------------------------------------------------- #
# Sauvegarde
# --------------------------------------------------------------------------- #
@app.get("/api/export")
def api_export():
    db.get_data()  # garantit l'existence du fichier
    return send_file(db.DB_PATH, as_attachment=True, download_name="bugs.json",
                     mimetype="application/json")


if __name__ == "__main__":
    # Serveur de dev (pratique pour tester). Pour un usage sur Raspberry Pi
    # avec plusieurs utilisateurs, préférez waitress (voir README).
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
