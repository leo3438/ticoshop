"""Génération du index.json lu par le Store de tico (0.8.0+).

Le format a été relevé dans tico 0.8.0 (« Creating a Store Repository ») :

    {
      "schema": 1,                     # tico refuse > 1 (« Needs a newer tico »)
      "name", "description", "maintainer", "website", "icon", "cover",
      "packages": [
        {
          "id", "name", "version", "author", "category", "tags",
          "tagline", "description", "changelog", "icon", "banner",
          "screenshots", "featured",
          "type",                      # zip | nro | rom | module
          "url" | "magnet" (+ "file"), "filename", "sha256", "size",
          "install", "extract", "extract_to_folder"
        }
      ]
    }

Toute la correspondance base -> JSON est ici : si une version de tico
change un nom de champ, c'est le seul fichier à adapter. Le champ « extra »
de chaque paquet est fusionné tel quel pour les champs non gérés.
"""
from typing import Any

SCHEMA_VERSION = 1

CATEGORIES = {
    "emulator": "Émulateur",
    "app": "Application",
    "homebrew": "Homebrew",
    "game": "Jeu",
    "port": "Port",
    "fangame": "Fan game",
    "soundpack": "Soundpack",
    "consolepack": "Console pack",
    "tool": "Outil",
    "other": "Autre",
}

TYPES = {
    "zip": "Archive zip (extraite)",
    "nro": "Homebrew .nro",
    "rom": "ROM / jeu",
    "module": "Module tico (émulateur)",
}

# Dossiers où tico refuse qu'un dépôt communautaire écrive
# (« This package tries to write outside the folders a community package may use »).
PROTECTED_PREFIXES = (
    "atmosphere", "bootloader", "switch/tico", "tico/modules", "tico/store", "tico/system",
)


def absolute(base_url: str, value: str) -> str:
    """Les chemins locaux (/media/…, /files/…) deviennent des URL absolues."""
    if not value:
        return ""
    if value.startswith(("http://", "https://", "magnet:")):
        return value
    return f"{base_url}/{value.lstrip('/')}"


def package_download_url(base_url: str, pkg: dict[str, Any]) -> str:
    if pkg["source"] == "upload" and pkg.get("local_file"):
        return f"{base_url}/files/{pkg['id']}/{pkg['filename']}"
    if pkg["source"] == "url":
        return pkg["url"]
    return ""


def is_downloadable(base_url: str, pkg: dict[str, Any]) -> bool:
    if pkg["source"] == "magnet":
        return pkg["magnet"].startswith("magnet:?")
    return package_download_url(base_url, pkg).startswith(("http://", "https://"))


def build_package(base_url: str, pkg: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": pkg["id"],
        "name": pkg["name"],
        "version": pkg["version"],
        "author": pkg["author"],
        "category": pkg["category"],
        "type": pkg["type"],
    }
    if pkg["tags"]:
        entry["tags"] = pkg["tags"]
    for key in ("tagline", "description", "changelog"):
        if pkg[key]:
            entry[key] = pkg[key]
    for key in ("icon", "banner"):
        if pkg[key]:
            entry[key] = absolute(base_url, pkg[key])
    if pkg["screenshots"]:
        entry["screenshots"] = [absolute(base_url, s) for s in pkg["screenshots"]]
    if pkg["featured"]:
        entry["featured"] = True

    if pkg["source"] == "magnet":
        entry["magnet"] = pkg["magnet"]
        if pkg["torrent_file"]:
            entry["file"] = pkg["torrent_file"]
    else:
        entry["url"] = package_download_url(base_url, pkg)
    if pkg["filename"]:
        entry["filename"] = pkg["filename"]
    if pkg["sha256"]:
        entry["sha256"] = pkg["sha256"].lower()
    if pkg["size"]:
        entry["size"] = int(pkg["size"])
    if pkg["install"]:
        entry["install"] = pkg["install"].strip("/")
    if pkg["extract"]:
        entry["extract"] = True
    if pkg["extract_to_folder"]:
        entry["extract_to_folder"] = True

    entry.update(pkg.get("extra") or {})
    return entry


def build_index(base_url: str, repo: dict[str, str], packages: list[dict[str, Any]]) -> dict[str, Any]:
    index: dict[str, Any] = {"schema": SCHEMA_VERSION, "name": repo["name"]}
    for key in ("description", "maintainer", "website"):
        if repo.get(key):
            index[key] = repo[key]
    for key in ("icon", "cover"):
        if repo.get(key):
            index[key] = absolute(base_url, repo[key])
    # Un paquet sans fichier téléchargeable n'est pas publié : une entrée
    # invalide pourrait faire rejeter tout l'index par tico.
    index["packages"] = [
        build_package(base_url, p) for p in packages if p["published"] and is_downloadable(base_url, p)
    ]
    return index


def validate_package(pkg: dict[str, Any]) -> list[str]:
    """Problèmes qui empêcheraient tico d'installer le paquet."""
    problems = []
    if pkg["source"] == "upload" and not pkg.get("local_file"):
        problems.append("Aucun fichier envoyé : le paquet n'apparaît pas dans index.json.")
    if pkg["source"] == "url" and not pkg["url"].startswith(("http://", "https://")):
        problems.append("L'URL de téléchargement doit commencer par http(s)://.")
    if pkg["source"] == "magnet" and not pkg["magnet"].startswith("magnet:?"):
        problems.append("Le lien magnet doit commencer par magnet:?.")
    if pkg["source"] != "magnet" and not pkg["sha256"]:
        problems.append("Pas de sha256 : tico ne pourra pas vérifier le téléchargement.")
    install = (pkg["install"] or "").strip("/")
    if ".." in install.split("/"):
        problems.append("Le dossier d'installation ne peut pas contenir « .. ».")
    if pkg["type"] != "module" and install.lower().startswith(PROTECTED_PREFIXES):
        problems.append(
            f"« {install} » est un dossier protégé : tico refusera l'installation "
            "depuis un dépôt communautaire."
        )
    if pkg["type"] == "module" and pkg["category"] != "emulator":
        problems.append("Un module tico devrait être dans la catégorie « emulator ».")
    return problems
