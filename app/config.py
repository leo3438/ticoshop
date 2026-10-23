"""Configuration lue depuis l'environnement (voir .env.example)."""
import os
import secrets
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("TICOSHOP_DATA_DIR", "./data")).resolve()
        # URL publique du shop, ex: https://shop.example.com (sans / final).
        # Sert à construire les liens absolus de index.json : tico télécharge
        # les fichiers et images depuis la Switch, il lui faut des URL complètes.
        self.public_url = os.environ.get("TICOSHOP_PUBLIC_URL", "").rstrip("/")
        self.admin_password = os.environ.get("TICOSHOP_ADMIN_PASSWORD", "")
        self.api_token = os.environ.get("TICOSHOP_API_TOKEN", "")
        self.secret_key = os.environ.get("TICOSHOP_SECRET_KEY") or secrets.token_hex(32)
        self.max_upload_mb = int(os.environ.get("TICOSHOP_MAX_UPLOAD_MB", "4096"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ticoshop.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.files_dir, self.media_dir):
            d.mkdir(parents=True, exist_ok=True)
