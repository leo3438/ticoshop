"""Configuration lue depuis l'environnement (voir .env.example)."""
import os
import secrets
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("TICOSHOP_DATA_DIR", "./data")).resolve()
        # URL publique du shop, ex: https://shop.example.com (sans / final).
        # Sert à construire les liens absolus de index.json : tico télécharge
        # les fichiers et images depuis la Switch, il lui faut des URL complètes.
        self.public_url = os.environ.get("TICOSHOP_PUBLIC_URL", "").rstrip("/")
        # Mot de passe du compte « admin » (toujours valable, même avec d'autres comptes).
        self.admin_password = os.environ.get("TICOSHOP_ADMIN_PASSWORD", "")
        self.api_token = os.environ.get("TICOSHOP_API_TOKEN", "")
        self.secret_key = os.environ.get("TICOSHOP_SECRET_KEY") or secrets.token_hex(32)
        self.max_upload_mb = _int("TICOSHOP_MAX_UPLOAD_MB", 4096)

        # Versions : nombre d'anciennes versions conservées par paquet.
        self.keep_versions = _int("TICOSHOP_KEEP_VERSIONS", 10)

        # Synchronisation des releases GitHub (0 = désactivée).
        self.github_token = os.environ.get("TICOSHOP_GITHUB_TOKEN", "")
        self.github_sync_hours = _int("TICOSHOP_GITHUB_SYNC_HOURS", 6)

        # Jaquettes automatiques (libretro-thumbnails) à l'envoi d'une ROM.
        self.auto_covers = os.environ.get("TICOSHOP_AUTO_COVERS", "1") not in ("0", "false", "no")
        self.covers_url = os.environ.get("TICOSHOP_COVERS_URL", "https://thumbnails.libretro.com").rstrip("/")

        # Renommage des ROM au nom officiel No-Intro (identification par CRC32).
        self.rom_rename = os.environ.get("TICOSHOP_ROM_RENAME", "1") not in ("0", "false", "no")
        self.dats_url = os.environ.get(
            "TICOSHOP_DATS_URL",
            "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/no-intro").rstrip("/")

        # Notification (Discord ou JSON générique) à chaque nouveauté.
        self.webhook_url = os.environ.get("TICOSHOP_WEBHOOK_URL", "")

        # Sauvegardes automatiques (0 = désactivées).
        backup_dir = os.environ.get("TICOSHOP_BACKUP_DIR", "")
        self.backup_dir = Path(backup_dir).resolve() if backup_dir else self.data_dir / "backups"
        self.backup_hours = _int("TICOSHOP_BACKUP_HOURS", 24)
        self.backup_keep = _int("TICOSHOP_BACKUP_KEEP", 7)

        # Anti brute-force de la page de connexion.
        self.login_max_attempts = _int("TICOSHOP_LOGIN_MAX_ATTEMPTS", 5)
        self.login_window_minutes = _int("TICOSHOP_LOGIN_WINDOW_MINUTES", 15)

        # Tâches de fond (sync GitHub, sauvegardes). Désactivées dans les tests.
        self.scheduler = os.environ.get("TICOSHOP_SCHEDULER", "1") not in ("0", "false", "no")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ticoshop.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def dats_dir(self) -> Path:
        return self.data_dir / "dats"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.files_dir, self.media_dir, self.backup_dir):
            d.mkdir(parents=True, exist_ok=True)
