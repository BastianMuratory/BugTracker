# Bugtrack — suivi de bugs & features

Application web légère de suivi de **bugs** et de **features**, écrite en
**Python / Flask**, avec une **base de données au format JSON** (facile à
sauvegarder et à éditer à la main). Conçue pour tourner sur un **Raspberry Pi**
et être utilisée par **plusieurs personnes en même temps**.

Aucune dépendance front-end externe : l'interface fonctionne hors-ligne, sans CDN.

---

## Fonctionnalités

- **Liste des bugs** (`/bugs`) — grille de cartes avec titre, ID, état,
  criticité, projet, responsable et mots-clés. Les bugs sont **triés par
  criticité** (`CRITIQUE` > `ÉLEVÉE` > `MOYENNE` > `FAIBLE`, puis du plus
  récemment modifié au plus ancien). Recherche et filtres (état / criticité).
- **Liste des features** (`/features`) — même présentation, mais pour les
  features ; elles sont triées par **priorité** (même échelle que la criticité).
- **Bouton « + Nouveau »** — ouvre une page de choix (`/new`) : **Bug** ou
  **Feature**. Chacun mène à son propre formulaire.
- **Fiche d'un bug** (`/bug/<id>`) — édition de toutes les informations
  (description, **lien NAS** unique, comportement observé / impact, comportement
  attendu, conditions et fréquence d'apparition), une **galerie d'images**
  jointes (glisser-déposer, sélection de fichier ou collage Ctrl+V d'une
  capture d'écran), plus un **tableau d'occurrences** avec compteur (lieu,
  personne, système, date).
- **Fiche d'une feature** (`/feature/<id>`) — champs adaptés aux features :
  **problème à résoudre** (besoin utilisateur), description, **lien NAS**,
  **bénéfice attendu**, **description fonctionnelle**, **critères d'acceptation**,
  plus les mêmes images et occurrences. Côté informations : état, **priorité**,
  projet associé, responsable et mots-clés.
- Les dates sont au **format européen JJ/MM/AAAA** (saisie et affichage ; elles
  restent stockées en ISO dans le fichier). Le champ **Projet** suggère
  uniquement les projets actifs (non archivés).
- **Tableau / Kanban** (`/board`) — colonnes = releases / projets. **Bugs et
  features y sont mélangés et traités de la même façon** (mêmes cartes, même
  glisser-déposer). Recherche dans la colonne « Non assigné » (par ID, titre
  **et mots-clés**), ajout d'un élément à une colonne par recherche, création de
  colonnes, et un menu « ⋯ » par colonne pour la **décaler à gauche/droite**, la
  renommer, l'**archiver** ou la supprimer.
- **Archives** (`/archived`) — projets terminés et archivés, avec leurs bugs et
  features ; chaque projet peut être **restauré** ou supprimé.
- **Sauvegarde** : bouton « Sauvegarde » qui télécharge la base JSON.

### Règle d'état automatique

Cette règle s'applique de la même façon aux **bugs et aux features**.

À la **création**, l'état par défaut est `BACKLOG` ; mais si un **projet est
associé** dès le départ, l'état devient `TODO` (travail planifié). Sur le
formulaire, choisir un projet bascule automatiquement un élément encore en
`BACKLOG` vers `TODO` (et inversement si on retire le projet) ; un état choisi
manuellement (`WIP`, `DONE`) n'est pas modifié.

De même, lorsqu'un élément est déplacé sur le tableau **de « Non assigné » vers
un projet**, s'il était en `BACKLOG` il passe automatiquement en `TODO`. À
l'inverse, **d'un projet vers « Non assigné »**, un élément en `TODO` repasse en
`BACKLOG`. (Cette règle de déplacement ne s'applique qu'au glisser-déposer ; sur
la fiche d'un élément, l'état que vous choisissez est toujours respecté tel quel.)


### Codes couleur

- **État** : `TODO`, `WIP`, `DONE`, `BACKLOG`.
- **Criticité (bugs) / Priorité (features)** (liseré coloré à gauche des cartes,
  même échelle partagée) :
  `CRITIQUE` (rouge), `ÉLEVÉE` (orange), `MOYENNE` (ambre), `FAIBLE` (bleu).

---

## Installation

Nécessite **Python 3.9+**.

```bash
# 1. (recommandé) créer un environnement virtuel
python3 -m venv venv
source venv/bin/activate        # sous Windows : venv\Scripts\activate

# 2. installer les dépendances
pip install -r requirements.txt
```

## Lancement

### En développement / pour tester

```bash
python app.py
```

L'application écoute sur `http://0.0.0.0:5000` (accessible depuis le réseau local
à l'adresse `http://<IP-du-raspberry>:5000`).

### En production sur le Raspberry Pi (plusieurs utilisateurs)

Le serveur de développement de Flask n'est pas prévu pour plusieurs utilisateurs
simultanés. Utilisez **waitress** (installé via `requirements.txt`) :

```bash
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

> Les écritures dans la base sont **atomiques** et protégées par un **verrou**
> (intra-processus + inter-processus via `flock`). Deux personnes qui modifient
> deux bugs différents ne s'écrasent donc pas. Sur un même bug, la **dernière
> sauvegarde gagne** (comportement volontaire pour ce projet).

### Démarrage automatique (optionnel, systemd)

Créer `/etc/systemd/system/bugtrack.service` :

```ini
[Unit]
Description=Bugtrack
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/bug-tracker
ExecStart=/home/pi/bug-tracker/venv/bin/waitress-serve --host=0.0.0.0 --port=5000 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Puis :

```bash
sudo systemctl enable --now bugtrack
```

---

## Base de données

Tout est stocké dans **`data/bugs.json`**. Au premier lancement, le fichier est
créé automatiquement avec quelques exemples (bugs et feature). Les images jointes
sont stockées séparément, comme fichiers, dans **`data/uploads/`** (seul leur nom
de fichier est référencé dans `bugs.json`).

Bugs et features sont stockés dans **la même liste** (`bugs`), ce qui permet de
les afficher ensemble sur le Tableau ; ils se distinguent par le champ **`kind`**
(`"bug"` ou `"feature"`). Les bugs ont un identifiant `BUG-NNN`, les features
`FEAT-NNN`. Le champ **`type`** porte la **criticité** (bug) ou la **priorité**
(feature) — même échelle `CRITIQUE / ÉLEVÉE / MOYENNE / FAIBLE`.

Structure :

```json
{
  "meta": { "version": 1 },
  "projects": ["Release 1.0", "Release 1.1"],
  "archived_projects": [],
  "bugs": [
    {
      "id": "BUG-001",
      "kind": "bug",
      "name": "…",
      "state": "WIP",
      "type": "CRITIQUE",
      "project": "Release 1.0",
      "responsible": "Alice",
      "keywords": ["login", "auth"],
      "description": "…",
      "nas_link": "\\\\nas\\bugs\\BUG-001\\",
      "observed_behavior": "…",
      "expected_behavior": "…",
      "conditions": "…",
      "frequency": "…",
      "images": ["3f9a1c2b8e7d4a56.png"],
      "occurrences": [
        { "id": "a1b2c3d4", "location": "…", "person": "…",
          "system": "…", "date": "2026-05-12" }
      ],
      "created_at": "…",
      "updated_at": "…"
    },
    {
      "id": "FEAT-001",
      "kind": "feature",
      "name": "…",
      "state": "BACKLOG",
      "type": "MOYENNE",
      "project": "Release 1.1",
      "responsible": "Alice",
      "keywords": ["ergonomie"],
      "problem": "…",
      "description": "…",
      "nas_link": "",
      "benefit": "…",
      "functional_description": "…",
      "acceptance_criteria": "…",
      "images": [],
      "occurrences": [],
      "created_at": "…",
      "updated_at": "…"
    }
  ]
}
```

> Note : les bases créées avant la séparation bugs/features sont **migrées
> automatiquement** au premier chargement : `kind` vaut `"bug"` par défaut, les
> anciens éléments de type `FEATURE` deviennent des features, et l'ancienne
> échelle (`MAJEUR`/`MODÉRÉ`/`MINEURE`) est convertie en
> `ÉLEVÉE`/`MOYENNE`/`FAIBLE`. (Les bases plus anciennes encore — sans lien NAS
> unique ni images — sont également migrées : le lien NAS d'une ancienne
> occurrence est repris au niveau de l'élément, `images` est initialisé à vide.)

### Sauvegarder

- Soit via le bouton **« Sauvegarde »** dans l'en-tête (télécharge `bugs.json`,
  hors images jointes).
- Soit en copiant le dossier : `cp -r data sauvegardes/data-$(date +%F)`
  (inclut `bugs.json` et les images de `data/uploads/`).

### Repartir de zéro

Arrêter l'application, puis remettre le contenu suivant dans `data/bugs.json` :

```json
{ "meta": { "version": 1 }, "projects": [], "archived_projects": [], "bugs": [] }
```

(ou supprimer le fichier : il sera recréé avec les données d'exemple au prochain
lancement). Pensez aussi à vider `data/uploads/` si vous voulez repartir sans
les anciennes images jointes.

---

## Structure du projet

```
bug-tracker/
├── app.py              # routes Flask (pages + API JSON)
├── database.py         # accès à la base JSON (verrous, écriture atomique)
├── requirements.txt
├── data/
│   ├── bugs.json       # la base (créée au 1er lancement) — bugs ET features
│   └── uploads/        # images jointes (créé à la 1ère image ajoutée)
├── templates/
│   ├── base.html       # gabarit commun (barre de navigation)
│   ├── bugs.html       # liste des bugs
│   ├── features.html   # liste des features
│   ├── new.html        # page de choix « bug ou feature ? »
│   ├── edit.html       # création / édition d'un bug
│   ├── feature_edit.html # création / édition d'une feature
│   ├── board.html      # tableau Kanban (bugs + features)
│   └── archived.html   # projets archivés
└── static/
    ├── css/style.css
    └── js/
        ├── common.js   # notifications + appels API
        ├── bugs.js     # filtrage des listes (bugs ET features)
        ├── edit.js     # formulaire bug ET feature (mots-clés, occurrences, images)
        ├── board.js    # glisser-déposer + colonnes (décaler, archiver…)
        └── archived.js # restauration / suppression des projets archivés
```
