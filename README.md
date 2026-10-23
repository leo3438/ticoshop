# ticoshop

Logiciel auto-hébergé pour gérer **votre propre dépôt (« shop ») pour le Store de [tico](https://github.com/ticohq/tico)** (tico 0.8.0+), le frontend d'émulation pour Nintendo Switch.

- **Admin web** (`/admin`) pour gérer les paquets : homebrews `.nro`, ports, jeux, fan games, soundpacks, console packs, modules d'émulateurs. Les fichiers sont hébergés sur le shop ou référencés par une URL externe ou un magnet. Le sha256 et la taille sont calculés automatiquement, et l'admin gère l'icône, la bannière, les captures, le changelog et le compteur de téléchargements.
- **`index.json` généré automatiquement** au format du Store de tico (`schema: 1`), à l'adresse `/index.json` (alias court `/t`).
- **Vitrine publique** avec le catalogue, une fiche par paquet et une page **« Ajouter à tico »** : l'URL à copier, un QR code et un one-liner bash pour la carte SD.
- **Docker** : une commande pour installer, `./scripts/install.sh`.
- **CLI bash** `scripts/ticoshop` pour tout gérer depuis un terminal ou une CI : upload, import depuis une release GitHub, validation et sauvegardes.

## Installation (serveur)

Prérequis : Docker + Docker Compose.

```bash
git clone https://github.com/leo3438/ticoshop.git && cd ticoshop
./scripts/install.sh --url https://shop.mondomaine.fr
```

Le script :
1. crée `.env`, avec un mot de passe admin, un jeton d'API et une clé de session aléatoires ;
2. construit l'image et démarre le conteneur (port 8080 par défaut, données dans `./data`) ;
3. affiche l'URL d'admin, le mot de passe et le jeton du CLI.

Pour mettre à jour, faites `git pull && ./scripts/install.sh`. Le `.env` et les données sont conservés.

> **Important :** `TICOSHOP_PUBLIC_URL` doit être l'adresse que **la Switch** peut joindre, car tico télécharge les fichiers et images avec les URL absolues de `index.json`. Sur un réseau local, `http://192.168.x.x:8080` fonctionne. Sur Internet, placez le shop derrière un reverse proxy HTTPS (Caddy, Traefik, Nginx…).

## Ajouter le shop sur une Switch

**Depuis la Switch** (méthode officielle) : tico › **Store** › **Sources** › **Ajouter un dépôt**, puis tapez `https://shop.mondomaine.fr/index.json`, ou la forme courte `https://shop.mondomaine.fr/t`.

**Depuis un PC, avec la carte SD** :

```bash
curl -fsSL https://shop.mondomaine.fr/add-to-switch.sh | bash -s -- /media/$USER/SWITCH
```

Le script ajoute l'URL dans `sdmc:/tico/config/store_repos.json`. Il crée d'abord une sauvegarde `.bak` du fichier, et une seconde exécution ne crée pas de doublon. Il fonctionne avec python3 ou jq, sous Linux, macOS (bash 3.2) et Git Bash.

La page `/add` du shop regroupe ces deux méthodes, avec un QR code, pour les partager facilement.

## Gérer le shop

### Interface web
`https://shop.mondomaine.fr/admin`, avec le mot de passe de `.env`. Le tableau de bord signale les paquets qui ne s'installeraient pas : fichier manquant, sha256 absent, dossier protégé, etc.

### CLI bash

```bash
export TICOSHOP_URL=https://shop.mondomaine.fr TICOSHOP_TOKEN=<jeton>   # ou ~/.ticoshop ; sur le serveur, .env est lu automatiquement

./scripts/ticoshop repo name="Mon shop" maintainer="Moi" description="Nos homebrews"
./scripts/ticoshop repo-media icon logo.png

# Homebrew hébergé sur le shop
./scripts/ticoshop add mon-app "Mon App" category=homebrew type=nro version=1.0 install=switch/mon-app
./scripts/ticoshop upload mon-app MonApp.nro
./scripts/ticoshop media mon-app icon icon.png
./scripts/ticoshop media mon-app screenshot capture1.jpg

# Paquet suivant la dernière release GitHub (URL, version, changelog, sha256)
./scripts/ticoshop add mon-module "Mon émulateur" category=emulator type=module
./scripts/ticoshop github mon-module moi/mon-emulateur 'module\.zip$'

./scripts/ticoshop list
./scripts/ticoshop validate      # code retour 2 s'il y a des problèmes : utilisable en CI
./scripts/ticoshop index         # affiche l'index.json publié
./scripts/ticoshop backup        # sur le serveur : archive data/
./scripts/ticoshop restore ticoshop-backup-….tar.gz
```

`./scripts/ticoshop help` affiche toutes les commandes et les clés disponibles. L'API REST est documentée sur `/api/docs` et s'utilise avec l'en-tête `Authorization: Bearer <jeton>`.

## Format de `index.json`

```json
{
  "schema": 1,
  "name": "Mon shop", "description": "…", "maintainer": "…", "website": "…",
  "icon": "https://…/media/….png", "cover": "https://…",
  "packages": [
    {
      "id": "mon-app", "name": "Mon App", "version": "1.0", "author": "…",
      "category": "homebrew", "type": "nro", "tags": ["…"],
      "tagline": "…", "description": "…", "changelog": "…",
      "icon": "https://…", "banner": "https://…", "screenshots": ["https://…"], "featured": true,
      "url": "https://…/files/mon-app/MonApp.nro", "filename": "MonApp.nro",
      "sha256": "…", "size": 123456,
      "install": "switch/mon-app", "extract": false, "extract_to_folder": false
    }
  ]
}
```

- `category` : `emulator`, `app`, `homebrew`, `game`, `port`, `fangame`, `soundpack`, `consolepack`, `tool`, `other`.
- `type` : `zip`, `nro`, `rom`, `module`.
- Un torrent utilise `magnet` (+ `file`) à la place de `url`.
- Pour un dépôt « communautaire », tico refuse d'écrire dans `atmosphere/`, `bootloader/`, `switch/tico`, `tico/modules`, etc. Seuls les dépôts de confiance peuvent livrer des émulateurs, sauf si l'utilisateur l'autorise dans Réglages › Store.

> Ce format a été relevé dans tico 0.8.0 : chaînes et code de `tico.nro`, notes de version. La documentation officielle se trouve dans « [Creating a Store Repository](https://forum.ticoverse.com/topic/150) ». Toute la correspondance se trouve dans `app/index.py`. Pour un champ ajouté par une future version de tico, utilisez « Champs supplémentaires » (JSON fusionné tel quel dans l'entrée), dans l'admin ou avec `extra='{…}'` en CLI.

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
TICOSHOP_ADMIN_PASSWORD=dev TICOSHOP_API_TOKEN=dev .venv/bin/uvicorn app.main:create_app --factory --reload --port 8080
```

L'application utilise FastAPI, SQLite et des templates Jinja, sans étape de build front.

## Légal

ticoshop ne fournit aucun contenu. Ne distribuez que ce que vous avez le droit de distribuer : homebrews, ports et contenus libres, ou vos propres créations. Les ROM commerciales, BIOS et firmwares sont exclus.
