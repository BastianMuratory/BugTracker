"""
Authentification — couche d'accès aux comptes utilisateurs.

Choix techniques (cohérents avec database.py) :
- Stockage dans un fichier JSON séparé (data/users.json), distinct de bugs.json.
- Mots de passe hashés avec werkzeug.security (scrypt) — jamais en clair,
  jamais en clair dans les logs ou le JSON.
- Pas de page d'inscription : les comptes sont créés uniquement en local par
  l'administrateur via le script manage_users.py (ligne de commande).
- Écriture atomique (fichier temporaire + os.replace), comme pour bugs.json.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from werkzeug.security import generate_password_hash, check_password_hash

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
USERS_PATH = DATA_DIR / "users.json"
USERS_LOCK_PATH = DATA_DIR / "users.json.lock"
USERS_TMP_PATH = DATA_DIR / "users.json.tmp"

_thread_lock = threading.RLock()


def _locked():
    """Verrou exclusif (threading + fcntl), même principe que database.py."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _thread_lock:
            if _HAS_FCNTL:
                f = open(USERS_LOCK_PATH, "w")
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    yield
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    f.close()
            else:
                yield
    return _cm()


def _read():
    if not USERS_PATH.exists():
        return {"users": []}
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = {"users": []}
    data.setdefault("users", [])
    return data


def _write(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(USERS_TMP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(USERS_TMP_PATH, USERS_PATH)


def list_usernames():
    with _locked():
        return [u["username"] for u in _read()["users"]]


def find_user(username):
    username = (username or "").strip().lower()
    with _locked():
        for u in _read()["users"]:
            if u["username"] == username:
                return u
    return None


def verify_login(username, password):
    """Renvoie le dict utilisateur si identifiants valides, sinon None."""
    user = find_user(username)
    if not user:
        return None
    if check_password_hash(user["password_hash"], password or ""):
        return user
    return None


def create_user(username, password):
    """Crée un compte. Renvoie (True, None) ou (False, message_erreur)."""
    username = (username or "").strip().lower()
    if not username:
        return False, "Nom d'utilisateur vide."
    if not password or len(password) < 8:
        return False, "Le mot de passe doit contenir au moins 8 caractères."
    with _locked():
        data = _read()
        if any(u["username"] == username for u in data["users"]):
            return False, "Ce nom d'utilisateur existe déjà."
        data["users"].append({
            "username": username,
            "password_hash": generate_password_hash(password),
        })
        _write(data)
        return True, None


def delete_user(username):
    username = (username or "").strip().lower()
    with _locked():
        data = _read()
        before = len(data["users"])
        data["users"] = [u for u in data["users"] if u["username"] != username]
        if len(data["users"]) != before:
            _write(data)
            return True
    return False


def change_password(username, new_password):
    username = (username or "").strip().lower()
    if not new_password or len(new_password) < 8:
        return False, "Le mot de passe doit contenir au moins 8 caractères."
    with _locked():
        data = _read()
        for u in data["users"]:
            if u["username"] == username:
                u["password_hash"] = generate_password_hash(new_password)
                _write(data)
                return True, None
    return False, "Utilisateur introuvable."
