# ticoshop

Logiciel auto-hébergé pour gérer **votre propre dépôt (« shop ») pour le Store de [tico](https://github.com/ticohq/tico)** (tico 0.8.0+), le frontend d'émulation pour Nintendo Switch.

| | |
|---|---|
| **Catalogue** | Homebrews `.nro`, ports, jeux, fan games, soundpacks, console packs, modules d'émulateurs. Fichier hébergé sur le shop, URL externe ou magnet. Le sha256 et la taille sont calculés automatiquement. Icône, bannière, captures. |
| **Versions** | Chaque nouveau fichier garde l'ancien dans l'historique. On peut **republier** une version en un clic et, en option, publier les anciennes versions dans tico. |
| **GitHub** | Un paquet lié à un dépôt suit ses releases : URL, version, changelog et sha256 sont mis à jour automatiquement. |
| **Modules tico** | En envoyant le `.zip` d'un module, le shop lit son `module.json` et pré-remplit la fiche : nom, consoles, type, catégorie. |
| **Vitrine** | Catalogue, fiches, page **« Ajouter à tico »** (URL, QR code, script carte SD) et flux RSS. Disponible en **français et en anglais**. |
| **Admin** | Plusieurs comptes, anti brute-force, statistiques (téléchargements, consultations du Store), **diagnostic** et sauvegardes. |
| **Notifications** | Webhook **Discord** (ou JSON générique) à chaque nouveau paquet ou mise à jour. |
| **Déploiement** | Docker, **HTTPS automatique** (Caddy + Let's Encrypt), image multi-arch publiée par la CI (amd64 / arm64). |
| **Bash** | `install.sh` pour installer, `ticoshop` (CLI) pour tout gérer, `add-to-switch.sh` pour la carte SD. |

## Installation (serveur)

Prérequis : Docker + Docker Compose.

```bash
git clone https://github.com/leo3438/ticoshop.git && cd ticoshop

./scripts/install.sh --domain shop.mondomaine.fr     # sur Internet, avec HTTPS automatique
./scripts/install.sh --url http://192.168.1.20:8080  # ou en réseau local
./scripts/install.sh --image                         # (option) image ghcr.io au lieu d'un build local
```

Le script crée `.env` (mot de passe admin, jeton d'API et clé de session aléatoires), démarre les conteneurs et affiche les accès. Pour mettre à jour, faites `git pull && ./scripts/install.sh` : le `.env` et les données sont conservés.

Avec `--domain`, le domaine doit pointer vers le serveur et les ports 80/443 doivent être ouverts. Caddy obtient et renouvelle le certificat tout seul.

Sans cloner le dépôt, l'image publiée suffit :

```bash
docker run -d --name ticoshop -p 8080:8080 -v ./data:/data -v ./backups:/backups \
  -e TICOSHOP_PUBLIC_URL=http://192.168.1.20:8080 -e TICOSHOP_ADMIN_PASSWORD=... -e TICOSHOP_API_TOKEN=... \
  ghcr.io/leo3438/ticoshop:latest
```

> `TICOSHOP_PUBLIC_URL` doit être l'adresse que **la Switch** peut joindre : tico télécharge les fichiers et images avec les URL absolues de `index.json`.

### Configuration (`.env`)

| Variable | Rôle | Défaut |
|---|---|---|
| `TICOSHOP_PUBLIC_URL` | Adresse publique du shop | — |
| `TICOSHOP_ADMIN_PASSWORD` | Mot de passe du compte `admin` | généré |
| `TICOSHOP_API_TOKEN` | Jeton de l'API / du CLI | généré |
| `TICOSHOP_DOMAIN`, `COMPOSE_PROFILES=https` | HTTPS via Caddy | — |
| `TICOSHOP_KEEP_VERSIONS` | Anciennes versions gardées par paquet | 10 |
| `TICOSHOP_GITHUB_SYNC_HOURS` / `TICOSHOP_GITHUB_TOKEN` | Fréquence de suivi GitHub (0 = off) / jeton optionnel (5000 req/h au lieu de 60) | 6 / — |
| `TICOSHOP_WEBHOOK_URL` | Webhook Discord ou URL JSON | — |
| `TICOSHOP_BACKUP_HOURS` / `TICOSHOP_BACKUP_KEEP` | Sauvegarde auto dans `./backups` (0 = off) / nombre gardé | 24 / 7 |
| `TICOSHOP_LOGIN_MAX_ATTEMPTS` / `TICOSHOP_LOGIN_WINDOW_MINUTES` | Anti brute-force par IP | 5 / 15 |
| `TICOSHOP_MAX_UPLOAD_MB` | Taille max d'un envoi | 4096 |
| `FORWARDED_ALLOW_IPS` | Proxys de confiance pour `X-Forwarded-For` (`*` avec Caddy) | 127.0.0.1 |

## Vérifier sur une vraie Switch (à faire en premier)

Le format de `index.json` a été relevé dans le binaire de tico 0.8.0, faute d'accès à la documentation officielle. La page **Admin › Diagnostic** vérifie la configuration et fournit un **dépôt de test** (`/diag/index.json`) contenant un seul paquet minimal :

1. tico › Store › Sources › Ajouter un dépôt › `https://votre-shop/diag/index.json` ;
2. le dépôt doit afficher 1 paquet ; installez-le : `sdmc:/switch/ticoshop-test/README.txt` doit apparaître ;
3. désinstallez-le et retirez la source.

## Ajouter le shop sur une Switch

- **Depuis la Switch** : tico › **Store** › **Sources** › **Ajouter un dépôt**, puis tapez `https://shop.mondomaine.fr/index.json` (ou la forme courte `/t`).
- **Depuis un PC, avec la carte SD** :
  ```bash
  curl -fsSL https://shop.mondomaine.fr/add-to-switch.sh | bash -s -- /media/$USER/SWITCH
  ```
  Le script ajoute l'URL dans `sdmc:/tico/config/store_repos.json`, avec une sauvegarde `.bak`. Une seconde exécution ne crée pas de doublon.

La page `/add` regroupe ces méthodes, avec un QR code. Partagez-la. `?lang=en` force l'anglais.

## Gérer le shop

### Interface web : `/admin`
Les onglets sont **Paquets**, **Dépôt**, **Statistiques**, **Diagnostic**, **Sauvegardes**, **Comptes** et **API** (documentation interactive sur `/api/docs`).

### CLI bash

```bash
export TICOSHOP_URL=https://shop.mondomaine.fr TICOSHOP_TOKEN=<jeton>   # ou ~/.ticoshop ; sur le serveur, .env est lu

./scripts/ticoshop repo name="Mon shop" maintainer="Moi" description="Nos homebrews"
./scripts/ticoshop repo-media icon logo.png

# Homebrew hébergé sur le shop
./scripts/ticoshop add mon-app "Mon App" category=homebrew type=nro install=switch/mon-app
./scripts/ticoshop upload mon-app MonApp.nro 1.0            # la version est jointe au fichier
./scripts/ticoshop media mon-app icon icon.png
./scripts/ticoshop upload mon-app MonApp.nro 1.1            # 1.0 passe dans l'historique
./scripts/ticoshop versions mon-app
./scripts/ticoshop rollback mon-app 1                      # republie la version RID 1

# Module d'émulateur : le module.json pré-remplit la fiche
./scripts/ticoshop add picodrive picodrive
./scripts/ticoshop upload picodrive tico-picodrive-module.zip 3.0.0

# Paquet qui suit les releases GitHub (vérifié toutes les TICOSHOP_GITHUB_SYNC_HOURS heures)
./scripts/ticoshop add mon-emu "Mon émulateur" category=emulator type=module
./scripts/ticoshop github mon-emu moi/mon-emulateur 'module\.zip$'
./scripts/ticoshop sync                                    # forcer une vérification

./scripts/ticoshop list
./scripts/ticoshop validate        # code retour 2 s'il y a des problèmes : utilisable en CI
./scripts/ticoshop stats 30
./scripts/ticoshop backup          # crée une sauvegarde sur le serveur et la télécharge
./scripts/ticoshop restore ticoshop-….tar.gz               # sur le serveur
```

`./scripts/ticoshop help` affiche toutes les commandes.

### Versions
- Une version est archivée **quand le fichier publié change**. Corriger seulement le numéro de version ne crée pas d'entrée. Envoyez donc le fichier avec son numéro (`upload ID FICHIER VERSION`, ou le formulaire admin).
- **Admin › Dépôt › « Publier aussi les anciennes versions »** ajoute les anciennes versions à `index.json` sous le même `id`, pour la vue « Versions » de tico. Cette option est **expérimentale**.

### Notifications Discord
Créez un webhook (Paramètres du salon › Intégrations › Webhooks), puis mettez son URL dans `TICOSHOP_WEBHOOK_URL`. Une notification est envoyée quand un paquet devient disponible ou change de version. Toute autre URL reçoit un JSON `{"event": "new"|"update", "package": {...}, "page", "index"}`.

### Sauvegardes
Une archive `tar.gz` (base SQLite copiée de façon cohérente, fichiers et images) est créée automatiquement dans `./backups`, avec rotation. Gardez aussi une copie **hors du serveur**, par exemple avec `ticoshop backup` lancé depuis un autre poste.

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

- `category` : `emulator`, `app`, `homebrew`, `game`, `port`, `fangame`, `soundpack`, `consolepack`, `tool`, `other`. `type` : `zip`, `nro`, `rom`, `module`.
- Un torrent utilise `magnet` (+ `file`) à la place de `url`. Un paquet sans fichier n'est pas publié.
- Pour un dépôt « communautaire », tico refuse d'écrire dans `atmosphere/`, `bootloader/`, `switch/tico`, `tico/modules`, etc. Les modules sont placés par tico lui-même ; l'utilisateur doit autoriser « Cores from any repository » dans Réglages › Store.

Toute la correspondance se trouve dans `app/index.py`. Pour un champ ajouté par une future version de tico, utilisez les « Champs supplémentaires » (JSON fusionné tel quel), dans l'admin ou avec `extra='{…}'` en CLI.

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
TICOSHOP_ADMIN_PASSWORD=dev TICOSHOP_API_TOKEN=dev .venv/bin/uvicorn app.main:create_app --factory --reload --port 8080
```

| Fichier | Rôle |
|---|---|
| `app/main.py` | Assemblage de l'application |
| `app/context.py` | État partagé |
| `app/routes_public.py`, `routes_admin.py`, `routes_api.py` | Routes |
| `app/db.py` | SQLite, migrations, versions, statistiques, comptes |
| `app/index.py` | Format tico |
| `app/github.py` | Suivi GitHub |
| `app/notify.py` | Webhooks |
| `app/backup.py` | Sauvegardes |
| `app/tasks.py` | Tâches de fond |
| `app/i18n.py` | Traductions |
| `app/diagnostic.py` | Dépôt de test |

La CI (`.github/workflows/ci.yml`) lance les tests et shellcheck, puis publie l'image `ghcr.io/<propriétaire>/ticoshop` sur `main` et sur les tags `v*`. Après la première publication, rendez le paquet public : GitHub › Packages › ticoshop › Package settings.

## Légal

ticoshop ne fournit aucun contenu. Ne distribuez que ce que vous avez le droit de distribuer : homebrews, ports et contenus libres, ou vos propres créations. Les ROM commerciales, BIOS et firmwares sont exclus.
