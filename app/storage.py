"""Fichiers des paquets et images : écriture en flux avec calcul du sha256."""
import hashlib
import re
import shutil
import urllib.request
import uuid
from pathlib import Path
from typing import BinaryIO

CHUNK = 1024 * 1024
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class UploadTooLarge(Exception):
    pass


def safe_filename(name: str) -> str:
    name = SAFE_NAME.sub("_", Path(name or "").name).strip("._")
    return name or "package.bin"


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
    """Remplace le fichier du paquet. Renvoie (chemin relatif, sha256, taille)."""
    folder = files_dir / pkg_id
    filename = safe_filename(filename)
    sha, size = write_stream(src, folder / filename, max_bytes)
    for old in folder.iterdir():
        if old.name != filename:
            old.unlink()
    return f"{pkg_id}/{filename}", sha, size


def delete_package_files(files_dir: Path, pkg_id: str) -> None:
    shutil.rmtree(files_dir / pkg_id, ignore_errors=True)


def save_image(media_dir: Path, filename: str, src: BinaryIO, max_bytes: int = 20 * CHUNK) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in IMAGE_EXTS:
        raise ValueError(f"Format d'image non pris en charge : {ext or '?'} (png, jpg, webp)")
    name = f"{uuid.uuid4().hex}{ext}"
    write_stream(src, media_dir / name, max_bytes)
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
