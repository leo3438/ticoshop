"""Sauvegardes : archive tar.gz de la base (copie cohérente), des fichiers et des images."""
import re
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from .config import Settings

NAME_RE = re.compile(r"^ticoshop-\d{8}-\d{6}\.tar\.gz$")


def create_backup(settings: Settings) -> Path:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    name = time.strftime("ticoshop-%Y%m%d-%H%M%S.tar.gz")
    target = settings.backup_dir / name
    part = target.with_name(name + ".part")
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "ticoshop.db"
        src = sqlite3.connect(settings.db_path)
        dst = sqlite3.connect(db_copy)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with tarfile.open(part, "w:gz") as tar:
            tar.add(db_copy, arcname="data/ticoshop.db")
            for folder in (settings.files_dir, settings.media_dir):
                if folder.exists():
                    tar.add(folder, arcname=f"data/{folder.name}")
    part.replace(target)
    rotate(settings)
    return target


def list_backups(settings: Settings) -> list[dict]:
    if not settings.backup_dir.exists():
        return []
    items = [p for p in settings.backup_dir.iterdir() if NAME_RE.match(p.name)]
    items.sort(key=lambda p: p.name, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size, "time": p.stat().st_mtime} for p in items]


def backup_path(settings: Settings, name: str) -> Path | None:
    if not NAME_RE.match(name):
        return None
    path = settings.backup_dir / name
    return path if path.is_file() else None


def rotate(settings: Settings) -> None:
    for item in list_backups(settings)[max(settings.backup_keep, 1):]:
        (settings.backup_dir / item["name"]).unlink(missing_ok=True)


def backup_due(settings: Settings) -> bool:
    if settings.backup_hours <= 0:
        return False
    backups = list_backups(settings)
    return not backups or time.time() - backups[0]["time"] >= settings.backup_hours * 3600
