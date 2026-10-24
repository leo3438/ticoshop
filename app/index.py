"""Génération du index.json lu par le Store de tico (0.8.0+).

Référence : « Creating a Store Repository » (forum.ticoverse.com/topic/150).

    {
      "schema": 1,
      "name", "description", "icon", "cover",
      "packages": [
        {
          "id", "name", "category" (app | game | port | soundpack), "tags" (2 max),
          "version", "author", "tagline", "description", "changelog",
          "icon", "banner", "screenshots" (8 max), "featured",
          "type" (zip | nro | module | rom), "url" | "magnet" (+ "file"),
          "size", "sha256", "install", "extract", "extract_to_folder", "filename"
        }
      ]
    }

tico écarte silencieusement un paquet invalide (id, url, type non autorisé,
checksum mal formé, ROM sans install/filename/size) : validate_package()
signale ces cas dans l'admin. Le champ « extra » de chaque paquet est fusionné
tel quel pour les champs non gérés.
"""
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import storage

SCHEMA_VERSION = 1

# Catégories du Store de tico.
CATEGORIES = {
    "app": "Application / homebrew",
    "game": "Jeu",
    "port": "Port",
    "soundpack": "Soundpack",
    "consolepack": "Console pack",
}
# Anciennes catégories de ticoshop, ramenées à celles de tico.
LEGACY_CATEGORIES = {"homebrew": "app", "tool": "app", "other": "app", "emulator": "app", "fangame": "game"}

TYPES = {
    "zip": "Archive zip",
    "nro": "Homebrew .nro",
    "rom": "ROM (convertie en zip à l'envoi)",
    "module": "Module tico (émulateur)",
}

# Dossiers où tico refuse toujours d'installer (en plus de la racine de la carte).
SYSTEM_FOLDERS = ("atmosphere", "bootloader", "nintendo", "emummc", "switch/tico",
                  "tico/system", "tico/modules", "tico/store", "tico/config")
ROM_MAX_SIZE = 64 * 1024 * 1024


def category_of(pkg: dict[str, Any]) -> str:
    return LEGACY_CATEGORIES.get(pkg["category"], pkg["category"]) or "app"


def tags_of(pkg: dict[str, Any]) -> list[str]:
    """Tags publiés : la console d'abord (rayon par console pour les jeux et ports), 2 au maximum."""
    tags = [t for t in pkg["tags"] if t]
    console = pkg.get("console") or ""
    if console and console not in tags and (category_of(pkg) in ("game", "port") or pkg["type"] == "rom"):
        tags.insert(0, console)
    return tags[:2]


def install_of(pkg: dict[str, Any]) -> str:
    """Dossier d'installation publié ; valeurs par défaut là où tico en exige un."""
    install = (pkg["install"] or "").strip().strip("/")
    if install:
        return install
    if pkg["type"] == "rom" and pkg.get("console"):
        return f"tico/roms/{pkg['console']}"
    if category_of(pkg) == "soundpack":
        return f"tico/theme/soundpacks/{pkg['id']}"
    return ""


def absolute(base_url: str, value: str) -> str:
    """Les chemins locaux (/media/…, /files/…) deviennent des URL absolues."""
    if not value:
        return ""
    if value.startswith(("http://", "https://", "magnet:")):
        return value
    return f"{base_url}/{value.lstrip('/')}"


def package_download_url(base_url: str, pkg: dict[str, Any]) -> str:
    if pkg["source"] == "upload" and pkg.get("local_file"):
        return f"{base_url}/files/{pkg['id']}/{quote(pkg['filename'])}"
    if pkg["source"] == "url":
        return pkg["url"]
    return ""


def release_download_url(base_url: str, pkg_id: str, rel: dict[str, Any]) -> str:
    """URL d'une ancienne version (voir l'historique des versions)."""
    if rel["source"] == "upload" and rel.get("local_file"):
        return f"{base_url}/files/{pkg_id}/r/{rel['rid']}/{quote(rel['filename'])}"
    if rel["source"] == "url":
        return rel["url"]
    return ""


def is_downloadable(base_url: str, pkg: dict[str, Any], download_url: str | None = None) -> bool:
    if pkg["source"] == "magnet":
        return pkg["magnet"].startswith("magnet:?")
    url = package_download_url(base_url, pkg) if download_url is None else download_url
    return url.startswith(("http://", "https://"))


def build_package(base_url: str, pkg: dict[str, Any], download_url: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": pkg["id"],
        "name": pkg["name"],
        "category": category_of(pkg),
        "type": pkg["type"],
    }
    tags = tags_of(pkg)
    if tags:
        entry["tags"] = tags
    for key in ("version", "author", "tagline", "description", "changelog"):
        if pkg[key]:
            entry[key] = pkg[key]
    for key in ("icon", "banner"):
        if pkg[key]:
            entry[key] = absolute(base_url, pkg[key])
    if pkg["screenshots"]:
        entry["screenshots"] = [absolute(base_url, s) for s in pkg["screenshots"][:8]]
    if pkg["featured"]:
        entry["featured"] = True

    if pkg["source"] == "magnet":
        entry["magnet"] = pkg["magnet"]
        if pkg["torrent_file"]:
            entry["file"] = pkg["torrent_file"]
    else:
        entry["url"] = package_download_url(base_url, pkg) if download_url is None else download_url
    if pkg["size"]:
        entry["size"] = int(pkg["size"])
    if pkg["sha256"]:
        entry["sha256"] = pkg["sha256"].lower()
    install = install_of(pkg)
    if install:
        entry["install"] = install
    keep = pkg["type"] == "zip" and pkg.get("keep_archive")
    if keep:
        entry["extract"] = False  # tico extrait par défaut
    if pkg["extract_to_folder"] and pkg["type"] == "zip" and not keep:
        entry["extract_to_folder"] = True
    if pkg["filename"] and (pkg["type"] == "rom" or keep):
        entry["filename"] = pkg["filename"]

    entry.update(pkg.get("extra") or {})
    return entry


def build_index(
    base_url: str,
    repo: dict[str, str],
    packages: list[dict[str, Any]],
    releases: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """releases : anciennes versions par paquet, publiées si repo["publish_versions"]."""
    index: dict[str, Any] = {"schema": SCHEMA_VERSION, "name": repo["name"]}
    for key in ("description", "maintainer", "website"):
        if repo.get(key):
            index[key] = repo[key]
    for key in ("icon", "cover"):
        if repo.get(key):
            index[key] = absolute(base_url, repo[key])
    # Un paquet sans fichier téléchargeable n'est pas publié : une entrée
    # invalide pourrait faire rejeter tout l'index par tico.
    entries = []
    for p in packages:
        if not p["published"] or not is_downloadable(base_url, p):
            continue
        entries.append(build_package(base_url, p))
        # Les anciennes versions gardent le même id : tico les regroupe
        # sous « Versions » sur la fiche du paquet.
        if repo.get("publish_versions") and releases:
            for rel in releases.get(p["id"], []):
                old = {**p, **{k: rel[k] for k in rel if k not in ("rid", "package_id", "created_at")},
                       "featured": False}
                url = release_download_url(base_url, p["id"], rel)
                if is_downloadable(base_url, old, url):
                    entries.append(build_package(base_url, old, url))
    index["packages"] = entries
    return index


def _under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "/")


def validate_package(pkg: dict[str, Any], files_dir: Path | None = None) -> list[str]:
    """Problèmes qui feraient écarter ou refuser le paquet par tico (et quelques conseils)."""
    problems = []
    category = category_of(pkg)
    if pkg["source"] == "upload" and not pkg.get("local_file"):
        problems.append("Aucun fichier envoyé : le paquet n'apparaît pas dans index.json.")
    if pkg["source"] == "url" and not pkg["url"].startswith(("http://", "https://")):
        problems.append("L'URL de téléchargement doit commencer par http(s)://.")
    if pkg["source"] == "magnet":
        if not pkg["magnet"].startswith("magnet:?"):
            problems.append("Le lien magnet doit commencer par magnet:?.")
        problems.append("Torrent : tico ne l'accepte d'un dépôt communautaire que si l'utilisateur active "
                        "Réglages › Store › Torrents from any repository.")
    if pkg["source"] != "magnet" and not pkg["sha256"]:
        problems.append("Pas de sha256 : tico ne pourra pas vérifier le téléchargement.")

    install = install_of(pkg)
    if install:
        if ".." in install.split("/") or ":" in install:
            problems.append("Le dossier d'installation doit être un chemin relatif simple (sans « .. »).")
        elif any(_under(install.lower(), f) for f in SYSTEM_FOLDERS):
            problems.append(f"« {install} » est un dossier système : tico refuse d'y installer "
                            f"({', '.join(SYSTEM_FOLDERS)}).")
    keep = pkg["type"] == "zip" and pkg.get("keep_archive")
    if (keep or pkg["extract_to_folder"]) and not install:
        problems.append("« Garder l'archive entière » et « Extraire dans un sous-dossier » exigent un dossier d'installation.")

    if pkg["type"] == "rom":
        problems.append("tico 0.8 masque les paquets « type: rom » d'un dépôt communautaire (constaté sur Switch) : "
                        "choisissez la console puis renvoyez le fichier, ticoshop l'emballera dans un zip "
                        "tico/roms/<console>/ que tico installe.")
        if not install:
            problems.append("ROM : choisissez la console (ou un dossier d'installation, ex. tico/roms/gba) : tico l'exige.")
        name = pkg["filename"] or ""
        if not name or "/" in name or "\\" in name or "." not in name:
            problems.append("ROM : le nom de fichier doit être un simple nom avec extension (ex. « Mon jeu (USA).gba »).")
        elif Path(name).suffix.lower() in (".zip", ".7z", ".rar"):
            problems.append("ROM : le type « rom » est pour un fichier brut ; pour une archive, utilisez le type zip "
                            "avec « Garder l'archive entière ».")
        if not pkg["size"]:
            problems.append("ROM : la taille est obligatoire.")
        elif int(pkg["size"]) > ROM_MAX_SIZE:
            problems.append("ROM : 64 Mio maximum pour le type « rom » ; utilisez le type zip au-delà.")

    if pkg["type"] == "module":
        if pkg["install"]:
            problems.append("Module : laissez le dossier d'installation vide (tico l'installe dans tico/modules/<id>).")
        if category != "app":
            problems.append("Module : le dépôt officiel les range en catégorie « app », avec le tag « emulator ».")
        problems.append("Module : tico ne l'accepte d'un dépôt communautaire que si l'utilisateur active "
                        "Réglages › Store › Cores from any repository.")

    if category == "soundpack" and not _under(install, f"tico/theme/soundpacks/{pkg['id']}"):
        problems.append(f"Soundpack : seul tico/theme/soundpacks/{pkg['id']} est autorisé.")
    if category in ("game", "port") and not tags_of(pkg):
        problems.append("Jeu / port : ajoutez la console (champ Console ou tag, ex. gba) pour qu'il soit rangé dans son rayon.")
    if len([t for t in pkg["tags"] if t]) > 2:
        problems.append("tico n'utilise que 2 tags : les suivants sont ignorés.")
    if len(pkg["screenshots"]) > 8:
        problems.append("tico affiche 8 captures au maximum.")

    # Un zip sans « install » est déployé depuis la racine de la carte SD.
    if (pkg["type"] == "zip" and not install and pkg["source"] == "upload" and pkg.get("local_file")
            and files_dir is not None):
        names = storage.zip_entries(files_dir / pkg["local_file"])
        if names is None:
            problems.append("Le fichier envoyé n'est pas un zip valide : choisissez le bon type (nro, rom…).")
        elif not names:
            problems.append("Le zip envoyé est vide.")
        else:
            allowed = (f"switch/{pkg['id']}/", f"tico/ports/{pkg['id']}/", "tico/roms/")
            outside = [n for n in names if not n.startswith(allowed)]
            if outside:
                problems.append(
                    f"Sans dossier d'installation, le zip est déployé depuis la racine de la carte SD et ne peut écrire "
                    f"que dans switch/{pkg['id']}/, tico/ports/{pkg['id']}/ ou tico/roms/ (ex. refusé : « {outside[0]} »). "
                    "Indiquez un dossier d'installation."
                )
    if pkg["type"] == "module" and pkg["source"] == "upload" and pkg.get("local_file") and files_dir is not None:
        names = storage.zip_entries(files_dir / pkg["local_file"])
        if names is not None and {n.split("/", 1)[0] for n in names} != {pkg["id"]}:
            problems.append(f"Module : le zip doit contenir un seul dossier nommé « {pkg['id']} ».")
    return problems
