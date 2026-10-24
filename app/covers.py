"""Jaquettes automatiques depuis la base libretro-thumbnails (celle qu'utilise tico).

Une ROM nommée selon No-Intro / Redump (« Super Mario World (USA).sfc ») est
cherchée dans https://thumbnails.libretro.com/<système>/Named_Boxarts/<nom>.png.
Les images sont copiées sur le shop : la Switch les récupère depuis l'index.
"""
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

# slug de console (dossiers tico/roms/<slug> et modules tico) -> (nom, système libretro)
CONSOLES: dict[str, tuple[str, str]] = {
    "nes": ("Nintendo Entertainment System", "Nintendo - Nintendo Entertainment System"),
    "snes": ("Super Nintendo", "Nintendo - Super Nintendo Entertainment System"),
    "n64": ("Nintendo 64", "Nintendo - Nintendo 64"),
    "gb": ("Game Boy", "Nintendo - Game Boy"),
    "gbc": ("Game Boy Color", "Nintendo - Game Boy Color"),
    "gba": ("Game Boy Advance", "Nintendo - Game Boy Advance"),
    "nds": ("Nintendo DS", "Nintendo - Nintendo DS"),
    "3ds": ("Nintendo 3DS", "Nintendo - Nintendo 3DS"),
    "vb": ("Virtual Boy", "Nintendo - Virtual Boy"),
    "gc": ("GameCube", "Nintendo - GameCube"),
    "wii": ("Wii", "Nintendo - Wii"),
    "master-system": ("Master System", "Sega - Master System - Mark III"),
    "game-gear": ("Game Gear", "Sega - Game Gear"),
    "genesis": ("Mega Drive / Genesis", "Sega - Mega Drive - Genesis"),
    "sega-cd": ("Sega CD", "Sega - Mega-CD - Sega CD"),
    "sega-32x": ("Sega 32X", "Sega - 32X"),
    "saturn": ("Sega Saturn", "Sega - Saturn"),
    "dc": ("Dreamcast", "Sega - Dreamcast"),
    "naomi": ("Naomi", "Sega - Naomi"),
    "atomiswave": ("Atomiswave", "Atomiswave"),
    "psx": ("PlayStation", "Sony - PlayStation"),
    "ps2": ("PlayStation 2", "Sony - PlayStation 2"),
    "psp": ("PlayStation Portable", "Sony - PlayStation Portable"),
    "pce": ("PC Engine / TurboGrafx-16", "NEC - PC Engine - TurboGrafx 16"),
    "wonderswan": ("WonderSwan", "Bandai - WonderSwan"),
    "wonderswan-color": ("WonderSwan Color", "Bandai - WonderSwan Color"),
    "atari-2600": ("Atari 2600", "Atari - 2600"),
    "msx": ("MSX", "Microsoft - MSX"),
    "fbneo": ("Arcade (FinalBurn Neo)", "FBNeo - Arcade Games"),
}

# Extensions sans ambiguïté -> console (les .iso / .bin / .chd ne permettent pas de deviner).
EXTENSIONS = {
    ".nes": "nes", ".fds": "nes", ".sfc": "snes", ".smc": "snes",
    ".n64": "n64", ".z64": "n64", ".v64": "n64",
    ".gb": "gb", ".gbc": "gbc", ".gba": "gba", ".nds": "nds", ".dsi": "nds",
    ".3ds": "3ds", ".cia": "3ds", ".cci": "3ds", ".vb": "vb",
    ".sms": "master-system", ".gg": "game-gear", ".md": "genesis", ".gen": "genesis",
    ".smd": "genesis", ".32x": "sega-32x", ".pce": "pce",
    ".ws": "wonderswan", ".wsc": "wonderswan-color", ".a26": "atari-2600",
    ".rvz": "gc", ".gcz": "gc", ".wbfs": "wii", ".cso": "psp",
}

KINDS = {"boxart": "Named_Boxarts", "title": "Named_Titles", "snap": "Named_Snaps"}
# Caractères remplacés par « _ » dans les noms de fichiers libretro.
FORBIDDEN = re.compile(r'[&*/:`<>?\\|"]')


def rom_name(filename: str, path: Path | None = None) -> tuple[str, str]:
    """(nom du jeu, extension) d'après le fichier envoyé ; regarde dans un .zip à une seule ROM."""
    name, ext = Path(filename).stem, Path(filename).suffix.lower()
    if ext == ".zip" and path is not None:
        try:
            with zipfile.ZipFile(path) as zf:
                roms = [n for n in zf.namelist() if not n.endswith("/") and Path(n).suffix.lower() in EXTENSIONS]
            if len(roms) == 1:
                return Path(roms[0]).stem, Path(roms[0]).suffix.lower()
        except (zipfile.BadZipFile, OSError):
            pass
    return name, ext


def guess_console(ext: str) -> str:
    return EXTENSIONS.get(ext.lower(), "")


def candidates(name: str) -> list[str]:
    """Variantes du nom à essayer, de la plus précise à la plus large."""
    base = re.sub(r"\s*\[[^\]]*\]", "", name).strip()          # [!], [b1]… (GoodTools)
    first_tag = re.sub(r"^(.*?\([^)]*\)).*$", r"\1", base)     # garde la 1re parenthèse (région)
    no_tags = re.sub(r"\s*\([^)]*\)", "", base).strip()
    out = []
    for n in (name, base, first_tag, no_tags):
        n = FORBIDDEN.sub("_", n).strip()
        if n and n not in out:
            out.append(n)
    return out


def thumbnail_url(base_url: str, console: str, kind: str, name: str) -> str:
    system = CONSOLES[console][1]
    return "/".join([
        base_url.rstrip("/"),
        urllib.parse.quote(system),
        KINDS[kind],
        urllib.parse.quote(f"{name}.png"),
    ])


class CoverServerError(Exception):
    """Le serveur de jaquettes est injoignable (≠ image absente)."""


def fetch(url: str, timeout: int = 20, max_bytes: int = 10 * 1024 * 1024) -> bytes | None:
    """L'image PNG, ou None si elle n'existe pas. CoverServerError si le serveur ne répond pas."""
    req = urllib.request.Request(url, headers={"User-Agent": "ticoshop"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 410):
            return None
        raise CoverServerError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CoverServerError(str(getattr(e, "reason", e))) from e
    if len(data) > max_bytes or not data.startswith(b"\x89PNG"):
        return None
    return data


def find_covers(base_url: str, console: str, name: str) -> tuple[str, dict[str, bytes]]:
    """Cherche boxart / title / snap. Renvoie (nom trouvé, {type: png}) ; vide si rien."""
    if console not in CONSOLES:
        return "", {}
    for candidate in candidates(name):
        boxart = fetch(thumbnail_url(base_url, console, "boxart", candidate))
        if boxart:
            images = {"boxart": boxart}
            for kind in ("title", "snap"):
                data = fetch(thumbnail_url(base_url, console, kind, candidate))
                if data:
                    images[kind] = data
            return candidate, images
    return "", {}
