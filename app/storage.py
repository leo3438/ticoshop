"""Fichiers des paquets et images : écriture en flux avec calcul du sha256."""
import hashlib
import json
import re
import shutil
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import BinaryIO

CHUNK = 1024 * 1024
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
# Garde les noms No-Intro (« Mon Jeu (USA, Europe) [!].gba ») ; retire séparateurs et caractères refusés.
UNSAFE_NAME = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')


class UploadTooLarge(Exception):
    pass


def safe_filename(name: str) -> str:
    name = UNSAFE_NAME.sub("_", Path((name or "").replace("\\", "/")).name).strip(" .")
    return name[:200] or "package.bin"


def write_stream(src: BinaryIO, dest: Path, max_bytes: int) -> tuple[str, int]:
    """Copie src dans dest et renvoie (sha256, taille). Écrit dans un .part puis renomme."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := src.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge()
                digest.update(chunk)
                out.write(chunk)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return digest.hexdigest(), size


def save_package_file(files_dir: Path, pkg_id: str, filename: str, src: BinaryIO, max_bytes: int) -> tuple[str, str, int]:
    """Enregistre un nouveau fichier dans files/<id>/<jeton>/<nom>.

    Chaque envoi a son propre dossier : les anciennes versions restent
    téléchargeables tant qu'elles sont dans l'historique.
    Renvoie (chemin relatif, sha256, taille).
    """
    filename = safe_filename(filename)
    token = uuid.uuid4().hex[:12]
    folder = files_dir / pkg_id / token
    try:
        sha, size = write_stream(src, folder / filename, max_bytes)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return f"{pkg_id}/{token}/{filename}", sha, size


def delete_package_files(files_dir: Path, pkg_id: str) -> None:
    shutil.rmtree(files_dir / pkg_id, ignore_errors=True)


def zip_entries(path: Path) -> list[str] | None:
    """Fichiers d'un zip (sans les dossiers), ou None si ce n'est pas un zip lisible."""
    try:
        with zipfile.ZipFile(path) as zf:
            return [n for n in zf.namelist() if not n.endswith("/")]
    except (zipfile.BadZipFile, OSError):
        return None


def inspect_module(path: Path) -> dict | None:
    """Lit le module.json d'une archive de module tico (ex: picodrive/module.json).

    Renvoie {"id", "name", "systems": [{"slug", "title", "short_title"}]} ou None.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            candidates = sorted(
                (n for n in zf.namelist() if n.rsplit("/", 1)[-1] == "module.json" and n.count("/") <= 2),
                key=lambda n: n.count("/"),
            )
            if not candidates:
                return None
            info = zf.getinfo(candidates[0])
            if info.file_size > 1024 * 1024:
                return None
            data = json.loads(zf.read(info).decode("utf-8"))
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    systems = [
        {k: str(s.get(k, "")) for k in ("slug", "title", "short_title")}
        for s in data.get("systems") or [] if isinstance(s, dict)
    ]
    return {"id": str(data["id"]), "name": str(data.get("name") or data["id"]), "systems": systems}


def save_image(media_dir: Path, filename: str, src: BinaryIO, max_bytes: int = 20 * CHUNK) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in IMAGE_EXTS:
        raise ValueError(f"Format d'image non pris en charge : {ext or '?'} (png, jpg, webp)")
    name = f"{uuid.uuid4().hex}{ext}"
    write_stream(src, media_dir / name, max_bytes)
    return f"/media/{name}"


def save_image_bytes(media_dir: Path, data: bytes, ext: str = ".png") -> str:
    name = f"{uuid.uuid4().hex}{ext}"
    (media_dir / name).write_bytes(data)
    return f"/media/{name}"


def delete_image(media_dir: Path, ref: str) -> None:
    if ref.startswith("/media/"):
        (media_dir / Path(ref).name).unlink(missing_ok=True)


def remote_checksum(url: str, max_bytes: int) -> tuple[str, int]:
    """Télécharge une URL externe (release GitHub…) pour en calculer sha256 et taille."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL http(s) attendue")
    req = urllib.request.Request(url, headers={"User-Agent": "ticoshop"})
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(req, timeout=60) as resp:
        while chunk := resp.read(CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise UploadTooLarge()
            digest.update(chunk)
    return digest.hexdigest(), size
