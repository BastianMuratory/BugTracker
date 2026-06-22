"""
Couche d'accès à la base de données JSON.

Choix techniques :
- Stockage dans un unique fichier JSON (data/bugs.json) : se sauvegarde en
  copiant le fichier, et s'édite à la main facilement.
- Accès concurrent sûr : verrou threading (intra-processus) + verrou fcntl
  (inter-processus, p.ex. plusieurs personnes / plusieurs workers en parallèle).
- Écritures ATOMIQUES (fichier temporaire + os.replace) : la base ne peut pas
  être corrompue, même si deux écritures arrivent en même temps.
- Sémantique "dernière écriture gagne" au niveau de chaque ressource : deux
  personnes qui modifient DEUX bugs différents ne s'écrasent pas ; deux
  personnes sur LE MÊME bug -> la dernière sauvegarde gagne (comportement
  accepté pour ce projet).
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl  # POSIX (Raspberry Pi / Linux / macOS)
    _HAS_FCNTL = True
except ImportError:  # Windows -> verrou threading seul
    _HAS_FCNTL = False

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "bugs.json"
LOCK_PATH = DATA_DIR / "bugs.json.lock"
TMP_PATH = DATA_DIR / "bugs.json.tmp"

# Valeurs autorisées (servent aussi à peupler les menus déroulants côté serveur)
VALID_STATES = ["TODO", "WIP", "DONE", "BACKLOG"]

# Niveaux de criticité (bugs) / priorité (features) : même échelle, partagée.
# C'est aussi la valeur du champ "type" qui pilote la couleur (liseré + badge).
VALID_TYPES = ["CRITIQUE", "ÉLEVÉE", "MOYENNE", "FAIBLE"]

# Deux natures d'éléments, stockés dans la même liste (pour un Tableau commun),
# mais avec des champs et des pages d'édition différents.
VALID_KINDS = ["bug", "feature"]
DEFAULT_KIND = "bug"

# État attribué par défaut à un nouvel élément (création).
DEFAULT_STATE = "BACKLOG"
DEFAULT_TYPE = "MOYENNE"

# Correspondance des anciennes valeurs de "type" vers la nouvelle échelle
# (migration automatique des bases créées avant la séparation bugs/features).
LEGACY_TYPE_MAP = {"MAJEUR": "ÉLEVÉE", "MODÉRÉ": "MOYENNE", "MINEURE": "FAIBLE"}

# Champs texte propres aux BUGS.
BUG_TEXT_FIELDS = [
    "description",
    "observed_behavior",   # Comportement observé / Impact opérationnel
    "expected_behavior",   # Comportement attendu
    "conditions",          # Conditions d'apparition
    "frequency",           # Fréquence d'apparition
    "nas_link",            # Lien NAS unique (partagé bug/feature)
]

# Champs texte propres aux FEATURES.
FEATURE_TEXT_FIELDS = [
    "problem",                 # Problème à résoudre (besoin utilisateur)
    "description",             # Description de la feature (comme les bugs)
    "nas_link",                # Lien NAS (comme les bugs)
    "benefit",                 # Bénéfice attendu
    "functional_description",  # Description fonctionnelle (que doit faire le système ?)
    "acceptance_criteria",     # Critères d'acceptation
]

# Liste blanche globale (union) utilisée par _normalize : un champ n'est écrit
# que s'il est effectivement présent dans le payload, donc une fiche bug ne se
# retrouve pas polluée par des champs de feature et inversement.
TEXT_FIELDS = list(dict.fromkeys(BUG_TEXT_FIELDS + FEATURE_TEXT_FIELDS))

OCC_FIELDS = ["location", "person", "system", "date"]

UPLOADS_DIR = DATA_DIR / "uploads"
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

_thread_lock = threading.RLock()


# --------------------------------------------------------------------------- #
# Verrou (intra + inter processus) et entrées/sorties atomiques
# --------------------------------------------------------------------------- #
@contextmanager
def _locked():
    """Verrou exclusif : threading (intra-processus) + fcntl (inter-processus)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _thread_lock:
        if _HAS_FCNTL:
            f = open(LOCK_PATH, "w")
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                f.close()
        else:
            yield


def _default_data():
    return {"meta": {"version": 1}, "projects": [], "archived_projects": [], "bugs": []}


def _read():
    """Lit la base (le verrou est supposé déjà tenu). Crée + amorce si absente."""
    if not DB_PATH.exists():
        data = _seed_data()
        _write(data)
        return data
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Fichier illisible : on repart d'une base vide au lieu de planter.
        data = _default_data()
    data.setdefault("meta", {"version": 1})
    data.setdefault("projects", [])
    data.setdefault("archived_projects", [])
    data.setdefault("bugs", [])
    _migrate_bugs(data["bugs"])
    return data


def _migrate_bugs(bugs):
    """Compatibilité avec les bases créées avant l'ajout des images, du lien NAS
    unique et de la séparation bugs/features :
      - ajoute "images" si absent ;
      - récupère un éventuel lien NAS présent sur une occurrence (ancien format) ;
      - ajoute "kind" ("bug" par défaut) ;
      - convertit les anciens éléments de type FEATURE en kind="feature" ;
      - remappe l'ancienne échelle de sévérité (MAJEUR/MODÉRÉ/MINEURE) vers la
        nouvelle (ÉLEVÉE/MOYENNE/FAIBLE).
    Opération idempotente : la relancer ne change plus rien.
    """
    for b in bugs:
        b.setdefault("images", [])
        if not b.get("nas_link"):
            for occ in b.get("occurrences", []):
                if isinstance(occ, dict) and occ.get("nas_link"):
                    b["nas_link"] = occ["nas_link"]
                    break
        b.setdefault("nas_link", "")
        for occ in b.get("occurrences", []):
            if isinstance(occ, dict):
                occ.pop("nas_link", None)

        # Nature de l'élément (bug/feature). Les anciens FEATURE deviennent des
        # features à part entière, avec une priorité par défaut.
        if b.get("type") == "FEATURE":
            b["kind"] = "feature"
            b["type"] = DEFAULT_TYPE
        b.setdefault("kind", DEFAULT_KIND)

        # Remap de l'échelle de criticité/priorité.
        if b.get("type") in LEGACY_TYPE_MAP:
            b["type"] = LEGACY_TYPE_MAP[b["type"]]
        if b.get("type") not in VALID_TYPES:
            b["type"] = DEFAULT_TYPE

        # Champs propres aux features (présents seulement sur les features).
        if b.get("kind") == "feature":
            for fld in FEATURE_TEXT_FIELDS:
                b.setdefault(fld, "")


def _write(data):
    """Écriture atomique (le verrou est supposé déjà tenu)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TMP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(TMP_PATH, DB_PATH)  # atomique sous POSIX


# --------------------------------------------------------------------------- #
# Helpers internes (verrou supposé tenu)
# --------------------------------------------------------------------------- #
def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _next_id(items, prefix):
    """Calcule le prochain identifiant <PREFIX>-NNN (robuste aux éditions
    manuelles). prefix = "BUG" pour un bug, "FEAT" pour une feature."""
    pfx = prefix.upper() + "-"
    max_n = 0
    for b in items:
        bid = str(b.get("id", "")).upper()
        if bid.startswith(pfx) and bid[len(pfx):].isdigit():
            max_n = max(max_n, int(bid[len(pfx):]))
    return f"{prefix}-{max_n + 1:03d}"


def _ensure_project(data, name):
    name = (name or "").strip()
    if (name and name not in data["projects"]
            and name not in data.get("archived_projects", [])):
        data["projects"].append(name)


def _clean_keywords(value):
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    elif isinstance(value, list):
        parts = [str(p).strip() for p in value]
    else:
        parts = []
    out = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return out


def _clean_images(value):
    """Liste blanche de noms de fichiers (déjà enregistrés via save_image)."""
    out = []
    if not isinstance(value, list):
        return out
    for item in value:
        name = str(item or "").strip()
        # sécurité minimale : pas de chemins, juste un nom de fichier simple
        if name and "/" not in name and "\\" not in name and name not in out:
            out.append(name)
    return out


def _clean_occurrences(value):
    out = []
    if not isinstance(value, list):
        return out
    for item in value:
        if not isinstance(item, dict):
            continue
        occ = {k: str(item.get(k, "")).strip() for k in OCC_FIELDS}
        occ["id"] = str(item.get("id") or uuid.uuid4().hex[:8])
        if any(occ[k] for k in OCC_FIELDS):  # on ignore les lignes entièrement vides
            out.append(occ)
    return out


def _normalize(payload, base=None):
    """Fusionne un payload dans un bug (liste blanche de champs + validation)."""
    bug = dict(base or {})
    if "name" in payload:
        bug["name"] = str(payload.get("name", "")).strip()
    if "state" in payload:
        st = str(payload.get("state", "")).upper()
        bug["state"] = st if st in VALID_STATES else (base or {}).get("state", DEFAULT_STATE)
    if "type" in payload:
        tp = str(payload.get("type", "")).upper()
        bug["type"] = tp if tp in VALID_TYPES else (base or {}).get("type", DEFAULT_TYPE)
    if "project" in payload:
        bug["project"] = str(payload.get("project", "")).strip()
    if "kind" in payload:
        kd = str(payload.get("kind", "")).lower()
        bug["kind"] = kd if kd in VALID_KINDS else (base or {}).get("kind", DEFAULT_KIND)
    if "responsible" in payload:
        bug["responsible"] = str(payload.get("responsible", "")).strip()
    if "keywords" in payload:
        bug["keywords"] = _clean_keywords(payload.get("keywords"))
    for fld in TEXT_FIELDS:
        if fld in payload:
            bug[fld] = str(payload.get(fld, ""))
    if "occurrences" in payload:
        bug["occurrences"] = _clean_occurrences(payload.get("occurrences"))
    if "images" in payload:
        bug["images"] = _clean_images(payload.get("images"))
    return bug


# --------------------------------------------------------------------------- #
# API publique
# --------------------------------------------------------------------------- #
def get_data():
    with _locked():
        return _read()


def list_all_items():
    """Tous les éléments (bugs ET features) — utilisé par le Tableau et les
    Archives, où les deux natures sont traitées de la même façon."""
    with _locked():
        return _read()["bugs"]


def list_bugs():
    """Uniquement les bugs (kind == 'bug')."""
    with _locked():
        return [b for b in _read()["bugs"] if b.get("kind", DEFAULT_KIND) == "bug"]


def list_features():
    """Uniquement les features (kind == 'feature')."""
    with _locked():
        return [b for b in _read()["bugs"] if b.get("kind") == "feature"]


def get_bug(item_id):
    """Récupère un élément par son identifiant (bug OU feature)."""
    with _locked():
        for b in _read()["bugs"]:
            if b.get("id") == item_id:
                return b
    return None


# Alias explicite : la recherche par id est indifférente à la nature.
get_item = get_bug


def _create(payload, kind):
    """Crée un élément (bug ou feature) et lui attribue un identifiant."""
    prefix = "FEAT" if kind == "feature" else "BUG"
    text_fields = FEATURE_TEXT_FIELDS if kind == "feature" else BUG_TEXT_FIELDS
    with _locked():
        data = _read()
        bug = _normalize(payload)
        bug["kind"] = kind
        bug["id"] = _next_id(data["bugs"], prefix)
        bug.setdefault("name", "")
        # État par défaut : BACKLOG, mais TODO si un projet est associé d'emblée
        # (cohérent avec la règle du tableau). N'intervient que si l'appelant n'a
        # pas fourni d'état explicite.
        bug.setdefault("state", "TODO" if (bug.get("project") or "").strip() else DEFAULT_STATE)
        bug.setdefault("type", DEFAULT_TYPE)
        bug.setdefault("project", "")
        bug.setdefault("responsible", "")
        bug.setdefault("keywords", [])
        for fld in text_fields:
            bug.setdefault(fld, "")
        bug.setdefault("occurrences", [])
        bug.setdefault("images", [])
        bug["created_at"] = _now()
        bug["updated_at"] = bug["created_at"]
        _ensure_project(data, bug["project"])
        data["bugs"].append(bug)
        _write(data)
        return bug


def create_bug(payload):
    return _create(payload, "bug")


def create_feature(payload):
    return _create(payload, "feature")


def update_bug(bug_id, payload):
    with _locked():
        data = _read()
        for i, b in enumerate(data["bugs"]):
            if b.get("id") == bug_id:
                old_images = set(b.get("images", []))
                merged = _normalize(payload, base=b)
                merged["id"] = b["id"]  # identifiant immuable
                merged["created_at"] = b.get("created_at", _now())
                merged["updated_at"] = _now()
                merged.setdefault("keywords", b.get("keywords", []))
                merged.setdefault("occurrences", b.get("occurrences", []))
                merged.setdefault("images", b.get("images", []))
                merged.setdefault("kind", b.get("kind", DEFAULT_KIND))
                data["bugs"][i] = merged
                _ensure_project(data, merged.get("project", ""))
                _write(data)
                removed_images = old_images - set(merged.get("images", []))
                for fname in removed_images:
                    delete_image_file(fname)
                return merged
    return None


def delete_bug(bug_id):
    with _locked():
        data = _read()
        before = len(data["bugs"])
        removed = [b for b in data["bugs"] if b.get("id") == bug_id]
        data["bugs"] = [b for b in data["bugs"] if b.get("id") != bug_id]
        if len(data["bugs"]) != before:
            _write(data)
            for b in removed:
                for fname in b.get("images", []):
                    delete_image_file(fname)
            return True
    return False


# La mise à jour, la suppression et le déplacement se font par identifiant et
# sont donc indifférents à la nature (bug ou feature) : alias pour la lisibilité.
update_item = update_bug
delete_item = delete_bug


# --------------------------------------------------------------------------- #
# Images jointes (stockées sur disque, référencées par nom de fichier)
# --------------------------------------------------------------------------- #
def save_image(file_storage, original_filename):
    """Enregistre un fichier image uploadé sous un nom unique et le renvoie.

    Le nom de fichier original n'est utilisé que pour récupérer l'extension ;
    le nom stocké est un identifiant aléatoire (pas de collision, pas de
    traversée de chemin). Si l'extension du fichier d'origine n'est pas
    reconnue (cas fréquent avec un copier-coller depuis le presse-papiers),
    on se base sur le type MIME envoyé par le navigateur.
    """
    ext = (original_filename or "").rsplit(".", 1)
    ext = ext[1].lower() if len(ext) == 2 else ""
    if ext not in IMAGE_EXTENSIONS:
        mimetype = getattr(file_storage, "mimetype", "") or ""
        guess = mimetype.split("/")[-1].lower() if "/" in mimetype else ""
        if guess == "jpg":
            guess = "jpeg"
        ext = guess if guess in IMAGE_EXTENSIONS else "png"
    name = f"{uuid.uuid4().hex}.{ext}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / name
    file_storage.save(dest)
    return name


def delete_image_file(filename):
    """Supprime un fichier image du disque (best-effort, n'échoue pas si absent)."""
    filename = str(filename or "").strip()
    if not filename or "/" in filename or "\\" in filename:
        return
    path = UPLOADS_DIR / filename
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def add_bug_image(bug_id, filename):
    """Ajoute un nom de fichier image à la liste d'un bug."""
    with _locked():
        data = _read()
        for b in data["bugs"]:
            if b.get("id") == bug_id:
                images = list(b.get("images", []))
                if filename not in images:
                    images.append(filename)
                b["images"] = images
                b["updated_at"] = _now()
                _write(data)
                return b
    return None


def remove_bug_image(bug_id, filename):
    """Retire un nom de fichier image de la liste d'un bug et supprime le fichier."""
    with _locked():
        data = _read()
        for b in data["bugs"]:
            if b.get("id") == bug_id:
                images = [f for f in b.get("images", []) if f != filename]
                if len(images) == len(b.get("images", [])):
                    return None  # le fichier n'était pas référencé sur ce bug
                b["images"] = images
                b["updated_at"] = _now()
                _write(data)
                delete_image_file(filename)
                return b
    return None


def set_bug_project(bug_id, project):
    """Déplace un bug vers un projet (utilisé par le glisser-déposer du tableau).

    Règle d'état au passage de/vers « Non assigné » :
      - « Non assigné » -> projet : un bug en BACKLOG passe en TODO (il devient
        du travail planifié).
      - projet -> « Non assigné » : un bug en TODO repasse en BACKLOG.
    """
    with _locked():
        data = _read()
        project = (project or "").strip()
        for b in data["bugs"]:
            if b.get("id") == bug_id:
                old = (b.get("project") or "").strip()
                if not old and project and b.get("state") == "BACKLOG":
                    b["state"] = "TODO"
                elif old and not project and b.get("state") == "TODO":
                    b["state"] = "BACKLOG"
                b["project"] = project
                b["updated_at"] = _now()
                _ensure_project(data, project)
                _write(data)
                return b
    return None


def list_projects():
    """Projets ACTIFS (non archivés) : persistés + référencés par un bug.

    L'ordre des projets persistés est conservé ; les projets archivés sont
    exclus (ils n'apparaissent plus sur le tableau ni dans les suggestions).
    """
    with _locked():
        data = _read()
        archived = set(data.get("archived_projects", []))
        projects = [p for p in data["projects"] if p not in archived]
        for b in data["bugs"]:
            p = (b.get("project") or "").strip()
            if p and p not in projects and p not in archived:
                projects.append(p)
        return projects


def list_archived_projects():
    """Projets archivés (terminés), dans leur ordre d'archivage."""
    with _locked():
        return list(_read().get("archived_projects", []))


def archive_project(name):
    """Archive un projet : il quitte le tableau et passe sur la page Archives.
    Ses bugs gardent leur projet (et réapparaissent si on le restaure)."""
    with _locked():
        data = _read()
        name = (name or "").strip()
        if not name:
            return False
        data["projects"] = [p for p in data["projects"] if p != name]
        if name not in data["archived_projects"]:
            data["archived_projects"].append(name)
        _write(data)
        return True


def restore_project(name):
    """Désarchive un projet : il revient sur le tableau (en fin de liste)."""
    with _locked():
        data = _read()
        name = (name or "").strip()
        if not name:
            return False
        data["archived_projects"] = [p for p in data["archived_projects"] if p != name]
        if name not in data["projects"]:
            data["projects"].append(name)
        _write(data)
        return True


def create_project(name):
    with _locked():
        data = _read()
        name = (name or "").strip()
        if not name:
            return None
        if name not in data["projects"]:
            data["projects"].append(name)
            _write(data)
        return name


def rename_project(old, new):
    with _locked():
        data = _read()
        old, new = (old or "").strip(), (new or "").strip()
        if not old or not new:
            return False
        renamed = [new if p == old else p for p in data["projects"]]
        if new not in renamed:
            renamed.append(new)
        seen = []
        for p in renamed:  # dédoublonnage en conservant l'ordre
            if p not in seen:
                seen.append(p)
        data["projects"] = seen
        for b in data["bugs"]:
            if (b.get("project") or "") == old:
                b["project"] = new
                b["updated_at"] = _now()
        _write(data)
        return True


def delete_project(name):
    """Supprime une colonne (active ou archivée) : ses bugs repassent en
    « Non assigné » (project="")."""
    with _locked():
        data = _read()
        name = (name or "").strip()
        data["projects"] = [p for p in data["projects"] if p != name]
        data["archived_projects"] = [p for p in data.get("archived_projects", []) if p != name]
        for b in data["bugs"]:
            if (b.get("project") or "") == name:
                b["project"] = ""
                b["updated_at"] = _now()
        _write(data)
        return True


def reorder_projects(order):
    with _locked():
        data = _read()
        order = [str(p).strip() for p in (order or []) if str(p).strip()]
        known = list(data["projects"])
        new_order = [p for p in order if p in known]
        for p in known:
            if p not in new_order:
                new_order.append(p)
        data["projects"] = new_order
        _write(data)
        return data["projects"]


# --------------------------------------------------------------------------- #
# Données d'exemple (au premier lancement, pour ne pas démarrer sur du vide).
# Pour repartir de zéro : arrêter l'app, vider data/bugs.json en mettant
#   {"meta": {"version": 1}, "projects": [], "bugs": []}
# --------------------------------------------------------------------------- #
def _seed_data():
    now = _now()
    return {
        "meta": {"version": 1},
        "projects": ["Release 1.0", "Release 1.1"],
        "archived_projects": [],
        "bugs": [
            {
                "id": "BUG-001",
                "name": "L'écran de connexion plante quand le mot de passe contient un espace",
                "kind": "bug",
                "state": "WIP",
                "type": "CRITIQUE",
                "project": "Release 1.0",
                "responsible": "Alice",
                "keywords": ["login", "auth", "crash"],
                "description": "L'application se ferme brutalement lors de la validation "
                               "du formulaire de connexion si le mot de passe saisi "
                               "contient au moins un caractère espace.",
                "observed_behavior": "Crash immédiat, retour à l'écran d'accueil du système. "
                                     "Aucun message d'erreur affiché à l'utilisateur.",
                "expected_behavior": "La connexion devrait réussir, ou afficher un message "
                                     "clair si le mot de passe est refusé.",
                "conditions": "Mot de passe contenant un espace. Reproductible à 100 %.",
                "frequency": "Systématique",
                "nas_link": "\\\\nas\\bugs\\BUG-001\\",
                "images": [],
                "occurrences": [
                    {
                        "id": "a1b2c3d4",
                        "location": "Poste accueil",
                        "person": "Bob",
                        "system": "Windows 11 / build 1042",
                        "date": "2026-05-12",
                    },
                    {
                        "id": "e5f6a7b8",
                        "location": "Atelier",
                        "person": "Chloé",
                        "system": "Windows 10 / build 1039",
                        "date": "2026-05-18",
                    },
                ],
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "BUG-002",
                "name": "L'export PDF tronque la dernière ligne du tableau",
                "kind": "bug",
                "state": "TODO",
                "type": "ÉLEVÉE",
                "project": "Release 1.0",
                "responsible": "David",
                "keywords": ["export", "pdf", "impression"],
                "description": "Lors de l'export PDF d'un tableau de plus de 30 lignes, "
                               "la dernière ligne est coupée en bas de page.",
                "observed_behavior": "Donnée manquante sur le document final imprimé.",
                "expected_behavior": "Toutes les lignes doivent apparaître, avec saut de "
                                     "page propre si besoin.",
                "conditions": "Tableaux longs (> 30 lignes).",
                "frequency": "Fréquent",
                "nas_link": "",
                "images": [],
                "occurrences": [],
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "FEAT-001",
                "name": "Ajouter un raccourci clavier pour la recherche",
                "kind": "feature",
                "state": "BACKLOG",
                "type": "MOYENNE",
                "project": "Release 1.1",
                "responsible": "Alice",
                "keywords": ["ergonomie", "raccourci", "recherche"],
                "problem": "Les utilisateurs perdent du temps à atteindre la barre "
                           "de recherche à la souris, surtout lors d'une utilisation "
                           "intensive au clavier.",
                "description": "Permettre d'ouvrir la barre de recherche avec Ctrl+K.",
                "nas_link": "",
                "benefit": "Gain de temps au quotidien et meilleure ergonomie pour "
                           "les utilisateurs avancés.",
                "functional_description": "Le raccourci Ctrl+K place le focus dans le "
                                          "champ de recherche, où que l'on soit dans "
                                          "l'application, sans recharger la page.",
                "acceptance_criteria": "Ctrl+K donne le focus à la recherche depuis "
                                       "n'importe quelle page ; Échap referme/retire le "
                                       "focus ; le raccourci n'entre pas en conflit avec "
                                       "ceux du navigateur.",
                "images": [],
                "occurrences": [],
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "BUG-004",
                "name": "Coquille dans l'info-bulle du bouton « Enregistrer »",
                "kind": "bug",
                "state": "DONE",
                "type": "FAIBLE",
                "project": "",
                "responsible": "Chloé",
                "keywords": ["ui", "texte"],
                "description": "« Enregister » au lieu de « Enregistrer ».",
                "observed_behavior": "Faute visible au survol du bouton.",
                "expected_behavior": "Orthographe correcte.",
                "conditions": "",
                "frequency": "Systématique",
                "nas_link": "",
                "images": [],
                "occurrences": [],
                "created_at": now,
                "updated_at": now,
            },
        ],
    }
