#!/usr/bin/env python3
"""
Gestion des comptes utilisateurs — outil en ligne de commande, à usage LOCAL
uniquement (pas exposé sur le réseau). C'est le seul moyen de créer un compte :
il n'y a pas de page d'inscription dans l'application.

Usage :
  python manage_users.py add <username>        # crée un compte (mot de passe demandé en saisie masquée)
  python manage_users.py passwd <username>     # change le mot de passe d'un compte existant
  python manage_users.py remove <username>     # supprime un compte
  python manage_users.py list                  # liste les comptes existants
"""
import getpass
import sys

import auth


def cmd_add(username):
    if not username:
        print("Usage : python manage_users.py add <username>")
        return 1
    pw1 = getpass.getpass("Mot de passe (8 caractères min.) : ")
    pw2 = getpass.getpass("Confirmer le mot de passe : ")
    if pw1 != pw2:
        print("Erreur : les deux mots de passe ne correspondent pas.")
        return 1
    ok, err = auth.create_user(username, pw1)
    if not ok:
        print("Erreur :", err)
        return 1
    print(f"Compte « {username} » créé avec succès.")
    return 0


def cmd_passwd(username):
    if not username:
        print("Usage : python manage_users.py passwd <username>")
        return 1
    if not auth.find_user(username):
        print(f"Erreur : utilisateur « {username} » introuvable.")
        return 1
    pw1 = getpass.getpass("Nouveau mot de passe (8 caractères min.) : ")
    pw2 = getpass.getpass("Confirmer le nouveau mot de passe : ")
    if pw1 != pw2:
        print("Erreur : les deux mots de passe ne correspondent pas.")
        return 1
    ok, err = auth.change_password(username, pw1)
    if not ok:
        print("Erreur :", err)
        return 1
    print(f"Mot de passe de « {username} » mis à jour.")
    return 0


def cmd_remove(username):
    if not username:
        print("Usage : python manage_users.py remove <username>")
        return 1
    if auth.delete_user(username):
        print(f"Compte « {username} » supprimé.")
        return 0
    print(f"Erreur : utilisateur « {username} » introuvable.")
    return 1


def cmd_list():
    users = auth.list_usernames()
    if not users:
        print("Aucun compte enregistré.")
        return 0
    print(f"{len(users)} compte(s) :")
    for u in users:
        print(" -", u)
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    action = args[0]
    rest = args[1:]

    if action == "add":
        return cmd_add(rest[0] if rest else None)
    if action == "passwd":
        return cmd_passwd(rest[0] if rest else None)
    if action == "remove":
        return cmd_remove(rest[0] if rest else None)
    if action == "list":
        return cmd_list()

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
