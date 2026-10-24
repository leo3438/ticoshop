# ticoshop

Logiciel auto-hébergé pour gérer **votre propre dépôt (« shop ») pour le Store de [tico](https://github.com/ticohq/tico)** (tico 0.8.0+), le frontend d'émulation pour Nintendo Switch.

| | |
|---|---|
| **Catalogue** | Homebrews `.nro`, ports, jeux, fan games, soundpacks, console packs, modules d'émulateurs. Fichier hébergé sur le shop, URL externe ou magnet. Le sha256 et la taille sont calculés automatiquement. Icône, bannière, captures. |
| **Versions** | Chaque nouveau fichier garde l'ancien dans l'historique. On peut **republier** une version en un clic et, en option, publier les anciennes versions dans tico. |
| **GitHub** | Un paquet lié à un dépôt suit ses releases : URL, version, changelog et sha256 sont mis à jour automatiquement. |
| **Jaquettes** | À l'envoi d'une ROM, la console est devinée d'après l'extension et la jaquette, l'écran titre et une capture sont récupérés automatiquement dans la base libretro, celle qu'utilise tico. Le bouton « Chercher la jaquette » de l'admin et `ticoshop cover` permettent de relancer la recherche. |
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
| `TICOSHOP_AUTO_COVERS` / `TICOSHOP_COVERS_URL` | Jaquettes automatiques à l'envoi d'une ROM / serveur libretro | 1 / thumbnails.libretro.com |
| `TICOSHOP_WEBHOOK_URL` | Webhook Discord ou URL JSON | — |
| `TICOSHOP_BACKUP_HOURS` / `TICOSHOP_BACKUP_KEEP` | Sauvegarde auto dans `./backups` (0 = off) / nombre gardé | 24 / 7 |
| `TICOSHOP_LOGIN_MAX_ATTEMPTS` / `TICOSHOP_LOGIN_WINDOW_MINUTES` | Anti brute-force par IP | 5 / 15 |
| `TICOSHOP_MAX_UPLOAD_MB` | Taille max d'un envoi | 4096 |
| `FORWARDED_ALLOW_IPS` | Proxys de confiance pour `X-Forwarded-For` (`*` avec Caddy) | 127.0.0.1 |

## Vérifier sur une vraie Switch (à faire en premier)

Le format de `index.json` a été relevé dans le binaire de tico 0.8.0, faute d'accès à la documentation officielle. Il a été **validé sur une Switch** pour un paquet `zip` installé dans `switch/<id>` : lecture de l'index, fiche, téléchargement, vérification du sha256 et extraction. La page **Admin › Diagnostic** vérifie la configuration et fournit un **dépôt de test** (`/diag/index.json`) contenant un seul paquet minimal :

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
./scripts/ticoshop add mon-app "Mon App" category=app type=nro
./scripts/ticoshop upload mon-app MonApp.nro 1.0            # la version est jointe au fichier
./scripts/ticoshop media mon-app icon icon.png
./scripts/ticoshop upload mon-app MonApp.nro 1.1            # 1.0 passe dans l'historique
./scripts/ticoshop versions mon-app
./scripts/ticoshop rollback mon-app 1                      # republie la version RID 1

# Module d'émulateur : le module.json pré-remplit la fiche
./scripts/ticoshop add picodrive picodrive
./scripts/ticoshop upload picodrive tico-picodrive-module.zip 3.0.0

# Paquet qui suit les releases GitHub (vérifié toutes les TICOSHOP_GITHUB_SYNC_HOURS heures)
./scripts/ticoshop add mon-emu "Mon émulateur" category=app type=module tags=emulator,gba
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

### Jaquettes automatiques
- La recherche se fait dans [libretro-thumbnails](https://github.com/libretro-thumbnails/libretro-thumbnails), d'après le **nom No-Intro / Redump** du fichier, par exemple `Super Mario World (USA).sfc`. Les tags `[!]` ou `(Rev 1)` sont retirés si besoin.
- La jaquette devient l'icône du paquet dans tico et sur la vitrine. L'écran titre sert de bannière et la capture est ajoutée aux captures. Les images sont copiées sur le shop.
- Si le nom ne correspond pas, indiquez le nom exact dans l'admin (Visuels › Jaquette automatique) ou avec `./scripts/ticoshop cover ID "Nom exact (Europe)" gba`.
- Pour désactiver la recherche automatique : `TICOSHOP_AUTO_COVERS=0`. Pour utiliser un miroir : `TICOSHOP_COVERS_URL`.

### Où s'installent les paquets
Les règles sont celles de la doc officielle, [Creating a Store Repository](https://forum.ticoverse.com/topic/150). L'admin et `ticoshop validate` les vérifient, y compris le contenu des zips envoyés.

| Paquet | Sans « Dossier d'installation » | Avec un dossier |
|---|---|---|
| `.nro` | `switch/<id>/<id>.nro` | `<dossier>/<id>.nro` |
| zip | déployé **depuis la racine de la SD** : n'écrit que dans `switch/<id>/`, `tico/ports/<id>/` ou `tico/roms/` | extrait dans le dossier ; tico demande confirmation |
| ROM (catégorie Jeu) | emballée par ticoshop dans un zip `tico/roms/<console>/<nom>` | — |
| soundpack | `tico/theme/soundpacks/<id>` (seul dossier autorisé) | — |
| module | `tico/modules/<id>`, à laisser vide | — |

Les dossiers système sont interdits : la racine, `atmosphere`, `bootloader`, `nintendo`, `emummc`, `switch/tico`, `tico/system`, `tico/modules`, `tico/store` et `tico/config`.

### Jeux et ROM
Uniquement des jeux que vous avez le droit de distribuer : homebrews, jeux gratuits dont la licence l'autorise, domaine public.

- **Envoyez simplement la ROM** (`.gb`, `.gba`, `.nes`…, ou un zip qui ne contient qu'elle), avec la catégorie **Jeu**. ticoshop devine la console d'après l'extension (sinon, choisissez-la), puis l'**emballe dans un zip** `tico/roms/<console>/<nom d'origine>`. C'est le format que tico 0.8 installe. Constaté sur Switch : tico masque les paquets `type: "rom"` venant d'un dépôt communautaire, alors que ce zip s'installe et le jeu apparaît dans Jeux.
- **Jeu lu zippé** par l'émulateur : type **zip**, dossier `tico/roms/<console>`, cochez **Garder l'archive entière** (`extract: false`). ticoshop ne réemballe pas ce zip.
- **Jeu en plusieurs fichiers** (`.cue` + `.bin`) : préparez un zip `tico/roms/<console>/<Jeu>/…`, ou utilisez un dossier d'installation `tico/roms/<console>` avec **Extraire dans un sous-dossier**. Dans tico, activez Réglages › Général › Scan Subfolders.
- La **console** devient le premier tag. tico range les jeux en rayons par console, d'après un nom ou un slug (`gba`, `snes`, `psx`/`playstation`…). tico ne lit que 2 tags.

### Console packs et soundpacks
Envoyez l'archive telle que l'auteur la distribue (**zip, rar ou 7z**), même au format « à coller à la racine de la SD » (`tico/assets/systems/covers/gba.jpg`…). Choisissez la catégorie **Console pack** ou **Soundpack** : ticoshop la convertit au format de tico 0.8.

- **Console pack** : les images vont dans `covers/`, `backgrounds/`, `logos/` et `symbols/`, `console_colors.json` devient `colors.json`, et le tout s'installe dans `tico/theme/consolepacks/<id>`. L'utilisateur le choisit dans Réglages › Interface › Console Pack, sans que ses fichiers ne soient écrasés.
- Un **soundpack** livré dans le même pack (`theme/soundpacks/<nom>/`) devient un paquet séparé, `<id>-sons`, installé dans `tico/theme/soundpacks/`.
- Les images d'aperçu de l'archive (`covers.jpg`, fond d'écran…) deviennent la bannière et les captures de la fiche.
- Ne publiez que des packs dont vous avez l'accord de l'auteur, en le créditant dans la description. Évitez les visuels repris de Nintendo (style « Switch Online »).

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

- `category` : `app`, `game`, `port`, `soundpack` (et `consolepack`). Les anciennes valeurs de ticoshop (`homebrew`, `tool`, `other`, `emulator` → `app` ; `fangame` → `game`) sont converties. `type` : `zip`, `nro`, `module`, `rom`.
- Les modules officiels sont en catégorie `app` avec `tags: ["emulator", "<console>"]`, sans `install`, `filename` ni `extract`.
- Un torrent utilise `magnet` (+ `file`) à la place de `url`. Un paquet sans fichier n'est pas publié.
- Voir « Où s'installent les paquets ». Les modules et les torrents d'un dépôt communautaire ne s'installent que si l'utilisateur les autorise dans Réglages › Store (« Cores from any repository », « Torrents from any repository »). `featured` n'est pris en compte que pour les dépôts de confiance.
- tico attend des images (`icon`, `banner`, `screenshots`) en `https://` : utilisez le HTTPS (`install.sh --domain`) pour qu'elles s'affichent.
- La console d'un jeu (`gba`, `snes`…) n'est utilisée que par ticoshop, pour les jaquettes. Elle n'est pas publiée dans l'index.

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
