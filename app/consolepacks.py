"""Conversion des packs de consoles et soundpacks vers les dossiers de tico 0.8.

Les packs de la communauté sont souvent distribués « à coller à la racine de la
SD » (tico/assets/systems/covers/gba.jpg…, tico/theme/console_colors.json).
Depuis tico 0.8, un Console Pack est un dossier tico/theme/consolepacks/<nom>/
avec covers/, backgrounds/, logos/ et symbols/ (+ colors.json), choisi dans
Réglages › Interface › Console Pack : rien n'est écrasé sur la carte.
"""
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

KINDS = ("covers", "backgrounds", "logos", "symbols")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".svg"}
PREVIEW_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
SOUND_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".json"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}


class PackError(Exception):
    pass


@dataclass
class Pack:
    files: dict[str, Path] = field(default_factory=dict)          # chemin dans le pack -> fichier
    soundpacks: dict[str, dict[str, Path]] = field(default_factory=dict)
    previews: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def consoles(self) -> list[str]:
        return sorted({PurePosixPath(p).stem for p in self.files if "/" in p})

    def count(self, kind: str) -> int:
        return sum(1 for p in self.files if p.startswith(kind + "/"))


def extract(archive: Path, dest: Path, max_bytes: int) -> None:
    """Extrait une archive zip / rar / 7z en refusant chemins absolus, « .. » et liens."""
    ext = archive.suffix.lower()
    if ext == ".zip":
        try:
            with zipfile.ZipFile(archive) as zf:
                total = 0
                for info in zf.infolist():
                    parts = PurePosixPath(info.filename.replace("\\", "/")).parts
                    if info.is_dir() or not parts or parts[0] in ("/", "") or ".." in parts:
                        continue
                    total += info.file_size
                    if total > max_bytes:
                        raise PackError("archive trop volumineuse une fois décompressée")
                    target = dest.joinpath(*parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out)
        except zipfile.BadZipFile as e:
            raise PackError("zip illisible") from e
        return
    if ext not in ARCHIVE_EXTS:
        raise PackError(f"format non pris en charge : {ext or '?'} (zip, rar, 7z)")
    if not shutil.which("bsdtar"):
        raise PackError("les archives .rar et .7z nécessitent bsdtar (paquet libarchive-tools)")
    # bsdtar refuse par défaut les chemins absolus et les « .. » (pas d'option -P).
    proc = subprocess.run(["bsdtar", "-xf", str(archive), "-C", str(dest), "--no-same-owner"],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise PackError(f"extraction impossible : {proc.stderr.strip()[:200]}")
    total = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file() and not p.is_symlink())
    if total > max_bytes:
        raise PackError("archive trop volumineuse une fois décompressée")


def classify(root: Path) -> Pack:
    """Repère images de consoles, couleurs, soundpacks et aperçus, quelle que soit l'arborescence."""
    pack = Pack()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = [p.lower() for p in PurePosixPath(rel).parts]
        name, suffix = path.name, path.suffix.lower()
        target = None
        for i in range(len(parts) - 2):
            # …/assets/systems/<kind>/<console>.<ext>  (format « racine de la SD »)
            if parts[i] == "assets" and parts[i + 1] == "systems" and parts[i + 2] in KINDS and i + 3 == len(parts) - 1:
                target = f"{parts[i + 2]}/{name}"
            # …/consolepacks/<pack>/<kind>/<console>.<ext>  (pack déjà au format 0.8)
            if parts[i] == "consolepacks" and i + 2 < len(parts) and parts[i + 2] in KINDS and i + 3 == len(parts) - 1:
                target = f"{parts[i + 2]}/{name}"
        if target is None and len(parts) >= 2 and parts[-2] in KINDS and len(parts) <= 3:
            target = f"{parts[-2]}/{name}"  # <kind>/<console>.<ext> à la racine
        if target and suffix in IMAGE_EXTS:
            pack.files[target] = path
            continue
        if name.lower() in ("console_colors.json", "colors.json"):
            pack.files["colors.json"] = path
            continue
        if "soundpacks" in parts and suffix in SOUND_EXTS:
            i = parts.index("soundpacks")
            if i + 2 < len(parts):
                sound_name = PurePosixPath(rel).parts[i + 1]
                inner = "/".join(PurePosixPath(rel).parts[i + 2:])
                pack.soundpacks.setdefault(sound_name, {})[inner] = path
                continue
        if suffix in PREVIEW_EXTS and ("systems" not in parts):
            pack.previews.append(path)  # covers.jpg d'aperçu, fond d'écran du thème…
            continue
        pack.skipped.append(rel)
    return pack


def write_zip(files: dict[str, Path], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, src in sorted(files.items()):
            zf.write(src, arcname)


def open_pack(archive: Path, max_bytes: int) -> tuple[Pack, tempfile.TemporaryDirectory]:
    """Extrait et classe l'archive. Le dossier temporaire doit être nettoyé par l'appelant."""
    tmp = tempfile.TemporaryDirectory(prefix="ticoshop-pack-")
    try:
        extract(archive, Path(tmp.name), max_bytes)
        return classify(Path(tmp.name)), tmp
    except BaseException:
        tmp.cleanup()
        raise
