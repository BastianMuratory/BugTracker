"""
Couche d'accès à la base de données JSON.

Choix techniques :
- Stockage réparti sur trois fichiers JSON, un par nature de donnée :
    - data/bugs.json     -> uniquement les bugs
    - data/features.json -> uniquement les features
    - data/projects.json -> projets actifs/archivés + leurs métadonnées (dates)
  (les comptes utilisateurs sont eux gérés séparément par auth.py, dans
  data/users.json). Chaque fichier se sauvegarde en copiant le fichier, et
  s'édite à la main facilement.
- Accès concurrent sûr : verrou threading (intra-processus) + verrou fcntl
  (inter-processus, p.ex. plusieurs personnes / plusieurs workers en parallèle)
  — un seul verrou couvre les trois fichiers, car certaines opérations
  (renommer/supprimer un projet, créer un élément) doivent les maintenir
  cohérents entre eux.
- Écritures ATOMIQUES (fichier temporaire + os.replace) : la base ne peut pas
  être corrompue, même si deux écritures arrivent en même temps.
- Sémantique "dernière écriture gagne" au niveau de chaque ressource : deux
  personnes qui modifient DEUX bugs différents ne s'écrasent pas ; deux
  personnes sur LE MÊME bug -> la dernière sauvegarde gagne (comportement
  accepté pour ce projet).

Compatibilité : les bases créées avant cette séparation (un unique
data/bugs.json contenant bugs ET features ET projets) sont migrées
automatiquement, une seule fois, au premier accès — voir _migrate_legacy().
"""
from __future__ import annotations

import json
import os
import shutil
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

BUGS_PATH = DATA_DIR / "bugs.json"
FEATURES_PATH = DATA_DIR / "features.json"
PROJECTS_PATH = DATA_DIR / "projects.json"
LEGACY_BACKUP_PATH = DATA_DIR / "bugs.legacy-backup.json"
LOCK_PATH = DATA_DIR / "db.lock"

# Conservé pour compatibilité (utilisé par d'anciens scripts éventuels) :
# pointe désormais sur le fichier des bugs, qui ne contient plus les features.
DB_PATH = BUGS_PATH

# Valeurs autorisées (servent aussi à peupler les menus déroulants côté serveur)
VALID_STATES = ["TODO", "WIP", "DONE", "BACKLOG"]

# Niveaux de criticité (bugs) / priorité (features) : même échelle, partagée.
# C'est aussi la valeur du champ "type" qui pilote la couleur (liseré + badge).
VALID_TYPES = ["CRITIQUE", "ÉLEVÉE", "MOYENNE", "FAIBLE"]

# Deux natures d'éléments, désormais stockées dans des fichiers séparés, mais
# avec des champs et des pages d'édition différents.
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

# Critères d'évaluation (note de 1 à 5 ; 0 = non noté). Communs aux bugs et aux
# features, ils permettent de pondérer / prioriser un élément. Stockés dans un
# sous-dictionnaire "criteria".
CRITERIA_FIELDS = [
    "product_importance",   # Importance produit
    "be_importance",        # Importance BE (bureau d'études)
    "users_impacted",       # Nombre d'utilisateurs impactés
    "urgency",              # Urgence
    "tech_effort",          # Effort technique
]

UPLOADS_DIR = DATA_DIR / "uploads"
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

_thread_lock = threading.RLock()

# Couples (lecture, écriture, clé de liste) pour itérer génériquement sur les
# deux stores d'éléments (bugs et features) sans dupliquer le code.
def _item_stores():
    return (
        (_read_bugs, _write_bugs, "bugs"),
        (_read_features, _write_features, "features"),
    )


# --------------------------------------------------------------------------- #
# Verrou (intra + inter processus) et entrées/sorties atomiques
# --------------------------------------------------------------------------- #
@contextmanager
def _locked():
    """Verrou exclusif : threading (intra-processus) + fcntl (inter-processus).

    Couvre les trois fichiers (bugs/features/projets) : certaines opérations
    doivent les maintenir cohérents entre eux (ex. renommer un projet met à
    jour bugs.json ET features.json ET projects.json).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _thread_lock:
        if _HAS_FCNTL:
            f = open(LOCK_PATH, "w")
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                _ensure_init()
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                f.close()
        else:
            _ensure_init()
            yield


def _ensure_init():
    """Amorce la base au premier accès (le verrou est supposé déjà tenu) :
    - base toute neuve -> données d'exemple, réparties sur les trois fichiers ;
    - ancienne base combinée (bugs.json contenant bugs+features+projets, sans
      features.json/projects.json) -> migration automatique, une seule fois.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if BUGS_PATH.exists() and not FEATURES_PATH.exists() and not PROJECTS_PATH.exists():
        _migrate_legacy()
    elif not BUGS_PATH.exists() and not FEATURES_PATH.exists() and not PROJECTS_PATH.exists():
        bugs_data, features_data, projects_data = _seed_data()
        _write_bugs(bugs_data)
        _write_features(features_data)
        _write_projects(projects_data)


def _migrate_legacy():
    """Scinde l'ancien fichier combiné (une seule liste "bugs" mélangeant bugs
    et features, plus "projects"/"archived_projects"/"project_meta" au même
    niveau) en trois fichiers. Une sauvegarde de l'ancien fichier est conservée
    (data/bugs.legacy-backup.json) avant réécriture, par précaution.
    """
    try:
        with open(BUGS_PATH, "r", encoding="utf-8") as f:
            legacy = json.load(f)
    except (json.JSONDecodeError, OSError):
        legacy = None
    if not isinstance(legacy, dict) or "bugs" not in legacy:
        return  # fichier vide/corrompu/déjà au nouveau format : rien à faire

    items = legacy.get("bugs", [])
    if not isinstance(items, list):
        items = []
    _migrate_legacy_items(items)
    bugs_list = [b for b in items if b.get("kind", DEFAULT_KIND) != "feature"]
    features_list = [b for b in items if b.get("kind") == "feature"]

    try:
        if not LEGACY_BACKUP_PATH.exists():
            shutil.copy2(BUGS_PATH, LEGACY_BACKUP_PATH)
    except OSError:
        pass  # la sauvegarde de précaution est best-effort, pas bloquante

    _write_bugs({"meta": {"version": 2}, "bugs": bugs_list})
    _write_features({"meta": {"version": 2}, "features": features_list})
    project_meta = legacy.get("project_meta", {})
    _write_projects({
        "meta": {"version": 2},
        "projects": legacy.get("projects", []) if isinstance(legacy.get("projects"), list) else [],
        "archived_projects": legacy.get("archived_projects", [])
            if isinstance(legacy.get("archived_projects"), list) else [],
        "project_meta": project_meta if isinstance(project_meta, dict) else {},
    })


def _migrate_legacy_items(items):
    """Normalisations historiques (avant la séparation bugs/features dans des
    fichiers distincts) : ajoute "images" si absent ; récupère un éventuel lien
    NAS présent sur une occurrence (ancien format) ; détermine "kind" ; remappe
    l'ancienne échelle de sévérité. N'est utilisée que lors de la migration
    ponctuelle depuis l'ancien fichier combiné — voir _migrate_legacy()."""
    for b in items:
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

        if b.get("kind") == "feature":
            for fld in FEATURE_TEXT_FIELDS:
                b.setdefault(fld, "")

        b["criteria"] = _clean_criteria(b.get("criteria"))


def _migrate_items(items, forced_kind):
    """Normalise les éléments d'un store homogène (déjà séparé par nature) à
    chaque lecture : idempotent, sans effet une fois la base à jour."""
    for b in items:
        b.setdefault("images", [])
        b.setdefault("nas_link", "")
        b["kind"] = forced_kind
        if b.get("type") in LEGACY_TYPE_MAP:
            b["type"] = LEGACY_TYPE_MAP[b["type"]]
        if b.get("type") not in VALID_TYPES:
            b["type"] = DEFAULT_TYPE
        if forced_kind == "feature":
            for fld in FEATURE_TEXT_FIELDS:
                b.setdefault(fld, "")
        b["criteria"] = _clean_criteria(b.get("criteria"))


def _read_json(path, default_factory):
    """Lit un fichier JSON (le verrou est supposé déjà tenu). Crée + amorce si
    absent ; repart d'une base vide au lieu de planter si illisible."""
    if not path.exists():
        data = default_factory()
        _write_json(path, data)
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default_factory()


def _write_json(path, data):
    """Écriture atomique (le verrou est supposé déjà tenu)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomique sous POSIX


def _read_bugs():
    data = _read_json(BUGS_PATH, lambda: {"meta": {"version": 2}, "bugs": []})
    data.setdefault("meta", {"version": 2})
    data.setdefault("bugs", [])
    _migrate_items(data["bugs"], "bug")
    return data


def _read_features():
    data = _read_json(FEATURES_PATH, lambda: {"meta": {"version": 2}, "features": []})
    data.setdefault("meta", {"version": 2})
    data.setdefault("features", [])
    _migrate_items(data["features"], "feature")
    return data


def _read_projects():
    data = _read_json(PROJECTS_PATH, lambda: {
        "meta": {"version": 2}, "projects": [], "archived_projects": [], "project_meta": {},
    })
    data.setdefault("meta", {"version": 2})
    data.setdefault("projects", [])
    data.setdefault("archived_projects", [])
    data.setdefault("project_meta", {})
    if not isinstance(data["project_meta"], dict):
        data["project_meta"] = {}
    return data


def _write_bugs(data):
    _write_json(BUGS_PATH, data)


def _write_features(data):
    _write_json(FEATURES_PATH, data)


def _write_projects(data):
    _write_json(PROJECTS_PATH, data)


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


def _ensure_project_store(name):
    """Référence un nom de projet dans projects.json s'il n'y figure pas déjà
    (actif ou archivé). Le verrou est supposé déjà tenu."""
    name = (name or "").strip()
    if not name:
        return
    data = _read_projects()
    if name not in data["projects"] and name not in data["archived_projects"]:
        data["projects"].append(name)
        _write_projects(data)


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


def _clean_criteria(value):
    """Normalise les critères : un dict {clé: note} avec les cinq clés connues.

    Chaque note est un entier de 1 à 5 ; toute valeur absente, hors bornes ou
    illisible est ramenée à 0 (« non noté »). Les clés inconnues sont ignorées.
    """
    out = {k: 0 for k in CRITERIA_FIELDS}
    if isinstance(value, dict):
        for k in CRITERIA_FIELDS:
            try:
                n = int(value.get(k, 0))
            except (TypeError, ValueError):
                n = 0
            out[k] = n if 1 <= n <= 5 else 0
    return out


def _clean_date(value):
    """Valide une date ISO (AAAA-MM-JJ). Renvoie "" si le format est invalide."""
    v = str(value or "").strip()
    if len(v) == 10 and v[4] == "-" and v[7] == "-":
        y, m, d = v[0:4], v[5:7], v[8:10]
        if y.isdigit() and m.isdigit() and d.isdigit():
            mo, da = int(m), int(d)
            if 1 <= mo <= 12 and 1 <= da <= 31:
                return v
    return ""


def _normalize(payload, base=None):
    """Fusionne un payload dans un bug/feature (liste blanche de champs +
    validation). La nature (kind) n'est pas modifiable via le payload : elle
    est fixée par le fichier de stockage (bugs.json ou features.json)."""
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
    if "criteria" in payload:
        bug["criteria"] = _clean_criteria(payload.get("criteria"))
    return bug


# --------------------------------------------------------------------------- #
# API publique
# --------------------------------------------------------------------------- #
def get_data():
    """Vue combinée (bugs + features + projets), au format de l'ancien fichier
    unique — utilisée par la sauvegarde/export (voir /api/export)."""
    with _locked():
        bugs = _read_bugs()
        features = _read_features()
        projects = _read_projects()
        return {
            "meta": {"version": 2},
            "projects": projects["projects"],
            "archived_projects": projects["archived_projects"],
            "project_meta": projects["project_meta"],
            "bugs": bugs["bugs"] + features["features"],
        }


def list_all_items():
    """Tous les éléments (bugs ET features) — utilisé par le Tableau et les
    Archives, où les deux natures sont traitées de la même façon."""
    with _locked():
        return _read_bugs()["bugs"] + _read_features()["features"]


def list_bugs():
    """Uniquement les bugs."""
    with _locked():
        return list(_read_bugs()["bugs"])


def list_features():
    """Uniquement les features."""
    with _locked():
        return list(_read_features()["features"])


def get_bug(item_id):
    """Récupère un élément par son identifiant (bug OU feature)."""
    with _locked():
        for b in _read_bugs()["bugs"]:
            if b.get("id") == item_id:
                return b
        for b in _read_features()["features"]:
            if b.get("id") == item_id:
                return b
    return None


# Alias explicite : la recherche par id est indifférente à la nature.
get_item = get_bug


def _create(payload, kind):
    """Crée un élément (bug ou feature) et lui attribue un identifiant."""
    prefix = "FEAT" if kind == "feature" else "BUG"
    text_fields = FEATURE_TEXT_FIELDS if kind == "feature" else BUG_TEXT_FIELDS
    read_fn, write_fn, items_key = (_read_features, _write_features, "features") \
        if kind == "feature" else (_read_bugs, _write_bugs, "bugs")
    with _locked():
        store = read_fn()
        items = store[items_key]
        bug = _normalize(payload)
        bug["kind"] = kind
        bug["id"] = _next_id(items, prefix)
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
        bug.setdefault("criteria", _clean_criteria(None))
        bug["created_at"] = _now()
        bug["updated_at"] = bug["created_at"]
        items.append(bug)
        write_fn(store)
        _ensure_project_store(bug["project"])
        return bug


def create_bug(payload):
    return _create(payload, "bug")


def create_feature(payload):
    return _create(payload, "feature")


def update_bug(bug_id, payload):
    with _locked():
        for read_fn, write_fn, items_key in _item_stores():
            store = read_fn()
            items = store[items_key]
            for i, b in enumerate(items):
                if b.get("id") == bug_id:
                    old_images = set(b.get("images", []))
                    merged = _normalize(payload, base=b)
                    merged["id"] = b["id"]                       # identifiant immuable
                    merged["kind"] = b.get("kind", DEFAULT_KIND)  # nature figée par le store
                    merged["created_at"] = b.get("created_at", _now())
                    merged["updated_at"] = _now()
                    merged.setdefault("keywords", b.get("keywords", []))
                    merged.setdefault("occurrences", b.get("occurrences", []))
                    merged.setdefault("images", b.get("images", []))
                    merged.setdefault("criteria", b.get("criteria") or _clean_criteria(None))
                    items[i] = merged
                    write_fn(store)
                    _ensure_project_store(merged.get("project", ""))
                    removed_images = old_images - set(merged.get("images", []))
                    for fname in removed_images:
                        delete_image_file(fname)
                    return merged
    return None


def delete_bug(bug_id):
    with _locked():
        for read_fn, write_fn, items_key in _item_stores():
            store = read_fn()
            items = store[items_key]
            before = len(items)
            removed = [b for b in items if b.get("id") == bug_id]
            store[items_key] = [b for b in items if b.get("id") != bug_id]
            if len(store[items_key]) != before:
                write_fn(store)
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
    """Ajoute un nom de fichier image à la liste d'un élément (bug ou feature)."""
    with _locked():
        for read_fn, write_fn, items_key in _item_stores():
            store = read_fn()
            for b in store[items_key]:
                if b.get("id") == bug_id:
                    images = list(b.get("images", []))
                    if filename not in images:
                        images.append(filename)
                    b["images"] = images
                    b["updated_at"] = _now()
                    write_fn(store)
                    return b
    return None


def remove_bug_image(bug_id, filename):
    """Retire un nom de fichier image de la liste d'un élément et supprime le
    fichier."""
    with _locked():
        for read_fn, write_fn, items_key in _item_stores():
            store = read_fn()
            for b in store[items_key]:
                if b.get("id") == bug_id:
                    images = [f for f in b.get("images", []) if f != filename]
                    if len(images) == len(b.get("images", [])):
                        return None  # le fichier n'était pas référencé sur cet élément
                    b["images"] = images
                    b["updated_at"] = _now()
                    write_fn(store)
                    delete_image_file(filename)
                    return b
    return None


def set_bug_project(bug_id, project):
    """Déplace un élément vers un projet (utilisé par le glisser-déposer du
    tableau).

    Règle d'état au passage de/vers « Non assigné » :
      - « Non assigné » -> projet : un élément en BACKLOG passe en TODO (il
        devient du travail planifié).
      - projet -> « Non assigné » : un élément en TODO repasse en BACKLOG.
    """
    with _locked():
        project = (project or "").strip()
        for read_fn, write_fn, items_key in _item_stores():
            store = read_fn()
            for b in store[items_key]:
                if b.get("id") == bug_id:
                    old = (b.get("project") or "").strip()
                    if not old and project and b.get("state") == "BACKLOG":
                        b["state"] = "TODO"
                    elif old and not project and b.get("state") == "TODO":
                        b["state"] = "BACKLOG"
                    b["project"] = project
                    b["updated_at"] = _now()
                    write_fn(store)
                    _ensure_project_store(project)
                    return b
    return None


def list_projects():
    """Projets ACTIFS (non archivés) : persistés + référencés par un bug/feature.

    L'ordre des projets persistés est conservé ; les projets archivés sont
    exclus (ils n'apparaissent plus sur le tableau ni dans les suggestions).
    """
    with _locked():
        proj = _read_projects()
        archived = set(proj["archived_projects"])
        projects = [p for p in proj["projects"] if p not in archived]
        for _read_fn, _write_fn, items_key in _item_stores():
            store = _read_fn()
            for b in store[items_key]:
                p = (b.get("project") or "").strip()
                if p and p not in projects and p not in archived:
                    projects.append(p)
        return projects


def list_archived_projects():
    """Projets archivés (terminés), dans leur ordre d'archivage."""
    with _locked():
        return list(_read_projects()["archived_projects"])


def archive_project(name):
    """Archive un projet : il quitte le tableau et passe sur la page Archives.
    Ses bugs/features gardent leur projet (et réapparaissent si on le restaure)."""
    with _locked():
        data = _read_projects()
        name = (name or "").strip()
        if not name:
            return False
        data["projects"] = [p for p in data["projects"] if p != name]
        if name not in data["archived_projects"]:
            data["archived_projects"].append(name)
        _write_projects(data)
        return True


def restore_project(name):
    """Désarchive un projet : il revient sur le tableau (en fin de liste)."""
    with _locked():
        data = _read_projects()
        name = (name or "").strip()
        if not name:
            return False
        data["archived_projects"] = [p for p in data["archived_projects"] if p != name]
        if name not in data["projects"]:
            data["projects"].append(name)
        _write_projects(data)
        return True


def create_project(name):
    with _locked():
        data = _read_projects()
        name = (name or "").strip()
        if not name:
            return None
        if name not in data["projects"]:
            data["projects"].append(name)
            _write_projects(data)
        return name


def rename_project(old, new):
    with _locked():
        old, new = (old or "").strip(), (new or "").strip()
        if not old or not new:
            return False
        data = _read_projects()
        renamed = [new if p == old else p for p in data["projects"]]
        if new not in renamed:
            renamed.append(new)
        seen = []
        for p in renamed:  # dédoublonnage en conservant l'ordre
            if p not in seen:
                seen.append(p)
        data["projects"] = seen
        # Suit le renommage côté métadonnées (dates de début/fin).
        pmeta = data["project_meta"]
        if old in pmeta and old != new:
            pmeta[new] = pmeta.pop(old)
        _write_projects(data)

        # Répercute le renommage sur les éléments concernés, dans les deux stores.
        for read_fn, write_fn, items_key in _item_stores():
            store = read_fn()
            changed = False
            for b in store[items_key]:
                if (b.get("project") or "") == old:
                    b["project"] = new
                    b["updated_at"] = _now()
                    changed = True
            if changed:
                write_fn(store)
        return True


def delete_project(name):
    """Supprime une colonne (active ou archivée) : ses bugs/features repassent
    en « Non assigné » (project="")."""
    with _locked():
        name = (name or "").strip()
        data = _read_projects()
        data["projects"] = [p for p in data["projects"] if p != name]
        data["archived_projects"] = [p for p in data["archived_projects"] if p != name]
        data["project_meta"].pop(name, None)
        _write_projects(data)

        for read_fn, write_fn, items_key in _item_stores():
            store = read_fn()
            changed = False
            for b in store[items_key]:
                if (b.get("project") or "") == name:
                    b["project"] = ""
                    b["updated_at"] = _now()
                    changed = True
            if changed:
                write_fn(store)
        return True


def reorder_projects(order):
    with _locked():
        data = _read_projects()
        order = [str(p).strip() for p in (order or []) if str(p).strip()]
        known = list(data["projects"])
        new_order = [p for p in order if p in known]
        for p in known:
            if p not in new_order:
                new_order.append(p)
        data["projects"] = new_order
        _write_projects(data)
        return data["projects"]


def get_project_meta():
    """Métadonnées des projets (dates de début/fin), indexées par nom de projet.

    Forme : { "Release 1.0": {"start_date": "AAAA-MM-JJ", "end_date": "..."} }.
    """
    with _locked():
        return dict(_read_projects()["project_meta"])


def set_project_dates(name, start_date, end_date):
    """Définit (ou efface) les dates de début/fin d'un projet.

    Les dates sont validées au format ISO (AAAA-MM-JJ). Si les deux sont vides,
    l'entrée est retirée. Renvoie un dict {name, start_date, end_date} ou None si
    le nom est invalide.
    """
    with _locked():
        data = _read_projects()
        name = (name or "").strip()
        if not name:
            return None
        start = _clean_date(start_date)
        end = _clean_date(end_date)
        meta = data["project_meta"]
        if start or end:
            meta[name] = {"start_date": start, "end_date": end}
        else:
            meta.pop(name, None)
        _write_projects(data)
        return {"name": name, "start_date": start, "end_date": end}


# --------------------------------------------------------------------------- #
# Données d'exemple (au premier lancement, pour ne pas démarrer sur du vide).
# Pour repartir de zéro : arrêter l'app, supprimer data/bugs.json,
# data/features.json et data/projects.json (ils seront recréés vides au
# prochain lancement — voir le README pour le détail).
# --------------------------------------------------------------------------- #
def _seed_data():
    """Renvoie les trois structures de données d'exemple (bugs, features,
    projets), prêtes à être écrites respectivement dans bugs.json,
    features.json et projects.json."""
    now = _now()
    bugs_data = {
        "meta": {"version": 2},
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
                "criteria": {"product_importance": 5, "be_importance": 4,
                             "users_impacted": 5, "urgency": 5, "tech_effort": 3},
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
                "criteria": {"product_importance": 4, "be_importance": 2,
                             "users_impacted": 3, "urgency": 3, "tech_effort": 2},
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
                "criteria": {"product_importance": 1, "be_importance": 1,
                             "users_impacted": 2, "urgency": 1, "tech_effort": 1},
                "occurrences": [],
                "created_at": now,
                "updated_at": now,
            },
        ],
    }

    features_data = {
        "meta": {"version": 2},
        "features": [
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
                "criteria": {"product_importance": 3, "be_importance": 3,
                             "users_impacted": 4, "urgency": 2, "tech_effort": 2},
                "occurrences": [],
                "created_at": now,
                "updated_at": now,
            },
        ],
    }

    projects_data = {
        "meta": {"version": 2},
        "projects": ["Release 1.0", "Release 1.1"],
        "archived_projects": [],
        "project_meta": {
            "Release 1.0": {"start_date": "2026-06-01", "end_date": "2026-06-30"},
            "Release 1.1": {"start_date": "2026-07-01", "end_date": "2026-08-15"},
        },
    }

    return bugs_data, features_data, projects_data
