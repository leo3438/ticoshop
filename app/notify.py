"""Notifications (Discord ou JSON générique) quand un paquet sort ou se met à jour."""
import json
import logging
import threading
import urllib.request
from typing import Any

from .db import has_download

log = logging.getLogger("ticoshop.notify")


def event_for(old: dict[str, Any] | None, new: dict[str, Any]) -> str | None:
    """« new » : le paquet devient visible ; « update » : nouvelle version publiée."""
    if not new["published"] or not has_download(new):
        return None
    if old is None or not old["published"] or not has_download(old):
        return "new"
    if (old["version"], old["sha256"], old["url"], old["local_file"], old["magnet"]) != (
        new["version"], new["sha256"], new["url"], new["local_file"], new["magnet"]
    ):
        return "update"
    return None


def payload(webhook_url: str, event: str, pkg: dict[str, Any], base_url: str, repo_name: str) -> dict[str, Any]:
    page = f"{base_url}/p/{pkg['id']}"
    version = f" v{pkg['version']}" if pkg["version"] else ""
    if "discord.com/api/webhooks" in webhook_url or "discordapp.com/api/webhooks" in webhook_url:
        title = f"{'🆕 Nouveau' if event == 'new' else '⬆️ Mise à jour'} : {pkg['name']}{version}"
        embed: dict[str, Any] = {
            "title": title,
            "url": page,
            "description": (pkg["tagline"] or pkg["changelog"] or pkg["description"])[:1000],
            "footer": {"text": repo_name},
        }
        if pkg["icon"]:
            icon = pkg["icon"] if pkg["icon"].startswith("http") else base_url + pkg["icon"]
            embed["thumbnail"] = {"url": icon}
        return {"username": repo_name[:80], "embeds": [embed]}
    return {
        "event": event,
        "repository": repo_name,
        "package": {k: pkg[k] for k in ("id", "name", "version", "category", "type", "tagline", "changelog")},
        "page": page,
        "index": f"{base_url}/index.json",
    }


def send(webhook_url: str, body: dict[str, Any]) -> None:
    def run() -> None:
        try:
            req = urllib.request.Request(
                webhook_url, data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json", "User-Agent": "ticoshop"},
            )
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as e:
            log.warning("webhook en échec : %s", e)

    threading.Thread(target=run, daemon=True).start()
