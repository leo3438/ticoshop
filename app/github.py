"""Suivi des releases GitHub : un paquet lié à un dépôt se met à jour tout seul."""
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from . import storage
from .db import Database

API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class SyncError(Exception):
    pass


def _get(url: str, token: str = "") -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ticoshop"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SyncError("dépôt ou release introuvable") from e
        if e.code in (403, 429):
            raise SyncError("limite de l'API GitHub atteinte (ajoutez TICOSHOP_GITHUB_TOKEN)") from e
        raise SyncError(f"GitHub a répondu {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise SyncError(f"GitHub injoignable : {e}") from e


def latest_release(repo: str, pattern: str = "", token: str = "") -> dict[str, Any]:
    """Dernière release publiée et l'asset qui correspond au motif (regex)."""
    if not REPO_RE.match(repo):
        raise SyncError("dépôt attendu sous la forme propriétaire/nom")
    release = _get(f"{API}/repos/{repo}/releases/latest", token)
    try:
        regex = re.compile(pattern or r"\.(zip|nro)$")
    except re.error as e:
        raise SyncError(f"motif d'asset invalide : {e}") from e
    assets = [a for a in release.get("assets", []) if regex.search(a.get("name", ""))]
    if not assets:
        names = ", ".join(a.get("name", "?") for a in release.get("assets", [])) or "aucun"
        raise SyncError(f"aucun asset ne correspond à « {regex.pattern} » (assets : {names})")
    asset = assets[0]
    return {
        "version": str(release.get("tag_name", "")).removeprefix("v"),
        "changelog": release.get("body") or "",
        "url": asset["browser_download_url"],
        "filename": asset["name"],
        "size": int(asset.get("size") or 0),
        "sha256": str(asset.get("digest") or "").removeprefix("sha256:"),
    }


def sync_package(db: Database, pkg: dict[str, Any], token: str, max_bytes: int, force: bool = False) -> str:
    """Met le paquet à jour si une nouvelle release existe. Renvoie un message d'état."""
    if not pkg["github_repo"]:
        raise SyncError("aucun dépôt GitHub lié à ce paquet")
    try:
        rel = latest_release(pkg["github_repo"], pkg["github_asset"], token)
        if not force and pkg["source"] == "url" and rel["url"] == pkg["url"] and pkg["sha256"]:
            db.update_package(pkg["id"], {"last_sync": time.time(), "sync_error": ""}, internal=True)
            return f"déjà à jour (v{pkg['version']})"
        if not rel["sha256"]:
            rel["sha256"], rel["size"] = storage.remote_checksum(rel["url"], max_bytes)
    except SyncError as e:
        db.update_package(pkg["id"], {"last_sync": time.time(), "sync_error": str(e)}, internal=True)
        raise
    except Exception as e:  # téléchargement du fichier pour le sha256
        db.update_package(pkg["id"], {"last_sync": time.time(), "sync_error": str(e)}, internal=True)
        raise SyncError(str(e)) from e
    db.update_package(
        pkg["id"],
        {**rel, "source": "url", "last_sync": time.time(), "sync_error": ""},
        internal=True,
    )
    return f"mis à jour en v{rel['version']}"


def sync_all(db: Database, token: str, max_bytes: int) -> dict[str, str]:
    results = {}
    for pkg in db.list_packages():
        if pkg["auto_update"] and pkg["github_repo"]:
            try:
                results[pkg["id"]] = sync_package(db, pkg, token, max_bytes)
            except SyncError as e:
                results[pkg["id"]] = f"erreur : {e}"
    return results
