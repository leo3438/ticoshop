"""Reconnaissance des ROM : nom officiel No-Intro d'après l'empreinte CRC32.

tico cherche les jaquettes (SteamGridDB, libretro) à partir du nom du jeu : un
fichier nommé « pkmn_crystal_FR_apfix.gbc » ne donne rien, alors que
« Pokemon - Version Cristal (France).gbc » est trouvé. On identifie donc chaque
ROM par son CRC32 dans les bases No-Intro de libretro-database (les mêmes que
celles de libretro / RetroArch) ; à défaut, le nom est nettoyé.

Utilisable sans le reste de l'application (scripts/rename-roms.py) : stdlib seule.
"""
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

DATS_URL = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/no-intro"

# slug de console tico -> fichier DAT No-Intro de libretro-database (consoles à cartouche).
SYSTEMS = {
    "nes": "Nintendo - Nintendo Entertainment System",
    "snes": "Nintendo - Super Nintendo Entertainment System",
    "n64": "Nintendo - Nintendo 64",
    "gb": "Nintendo - Game Boy",
    "gbc": "Nintendo - Game Boy Color",
    "gba": "Nintendo - Game Boy Advance",
    "nds": "Nintendo - Nintendo DS",
    "vb": "Nintendo - Virtual Boy",
    "master-system": "Sega - Master System - Mark III",
    "game-gear": "Sega - Game Gear",
    "genesis": "Sega - Mega Drive - Genesis",
    "sega-32x": "Sega - 32X",
    "sg-1000": "Sega - SG-1000",
    "pce": "NEC - PC Engine - TurboGrafx 16",
    "wonderswan": "Bandai - WonderSwan",
    "wonderswan-color": "Bandai - WonderSwan Color",
    "atari-2600": "Atari - 2600",
    "colecovision": "Coleco - ColecoVision",
}
# Noms de dossiers tico/roms/<…> rencontrés -> slug ci-dessus.
FOLDER_ALIASES = {
    "gameboy": "gb", "game-boy": "gb", "gameboy-color": "gbc", "gameboy-advance": "gba",
    "megadrive": "genesis", "mega-drive": "genesis", "md": "genesis", "32x": "sega-32x",
    "sms": "master-system", "gg": "game-gear", "ws": "wonderswan", "wsc": "wonderswan-color",
    "a26": "atari-2600", "atari2600": "atari-2600", "nintendo-64": "n64", "super-nintendo": "snes",
    "ds": "nds", "pcengine": "pce", "tg16": "pce",
}
# En-têtes de copieurs / formats retirés avant le calcul du CRC (No-Intro les ignore).
HEADERS = {".nes": 16, ".fds": 16, ".a78": 128, ".lnx": 64}

ROM_LINE = re.compile(r'rom\s*\(\s*name\s+"((?:[^"\\]|\\.)*)"\s+size\s+(\d+)\s+crc\s+([0-9A-Fa-f]{8})')

GOODTOOLS_REGIONS = {
    "U": "USA", "E": "Europe", "J": "Japan", "F": "France", "G": "Germany", "S": "Spain",
    "I": "Italy", "W": "World", "UE": "USA, Europe", "JU": "Japan, USA", "JUE": "World",
    "K": "Korea", "A": "Australia", "B": "Brazil", "C": "China", "NL": "Netherlands",
}


def system_for(console: str) -> str | None:
    console = FOLDER_ALIASES.get(console.lower(), console.lower())
    return SYSTEMS.get(console)


# --- bases DAT ----------------------------------------------------------------
def load_dat(console: str, cache_dir: Path, base_url: str = DATS_URL, timeout: int = 60) -> dict[str, str]:
    """{crc32 en majuscules: nom de fichier No-Intro} ; téléchargé une fois puis gardé en cache."""
    system = system_for(console)
    if not system:
        return {}
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{system}.dat"
    if not path.exists():
        url = f"{base_url.rstrip('/')}/{urllib.parse.quote(system)}.dat"
        req = urllib.request.Request(url, headers={"User-Agent": "ticoshop"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            return {}
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    return parse_dat(path.read_text(encoding="utf-8", errors="replace"))


def parse_dat(text: str) -> dict[str, str]:
    table: dict[str, str] = {}
    for name, _size, crc in ROM_LINE.findall(text):
        table.setdefault(crc.upper(), name.replace('\\"', '"'))
    return table


# --- empreintes ---------------------------------------------------------------
def _crc(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def crcs_of(data: bytes, ext: str) -> list[str]:
    """CRC32 du fichier, et sans en-tête quand le format en a un."""
    out = [_crc(data)]
    skip = HEADERS.get(ext.lower(), 0)
    if ext.lower() in (".sfc", ".smc") and len(data) % 1024 == 512:
        skip = 512  # en-tête de copieur SNES
    if skip and len(data) > skip:
        out.append(_crc(data[skip:]))
    return out


def identify(data: bytes, ext: str, table: dict[str, str]) -> str | None:
    for crc in crcs_of(data, ext):
        if crc in table:
            return table[crc]
    return None


# --- nettoyage à défaut d'identification ------------------------------------------
def clean_name(stem: str) -> str:
    """« 1234_-_super_mario_land_(E)_[!]_apfix » -> « Super Mario Land (Europe) »."""
    name = stem
    if " " not in name and ("_" in name or "." in name):
        name = re.sub(r"[_.]+", " ", name)
    name = re.sub(r"^\s*\d{3,5}\s*[-_ ]\s*", "", name)                     # numéro de release
    name = re.sub(r"\[[^\]]*\]", "", name)                                # [!] [b1] [T+Fre]…
    name = re.sub(r"[\s_-]*\b(apfix|ap fix|patched|decrypted|trimmed|nointro|goodset)\b", "", name, flags=re.I)

    def region(m: re.Match) -> str:
        code = m.group(1).upper()
        return f"({GOODTOOLS_REGIONS[code]})" if code in GOODTOOLS_REGIONS else m.group(0)

    name = re.sub(r"\(([A-Za-z]{1,3})\)", region, name)
    name = re.sub(r"\s{2,}", " ", name).strip(" -_.")
    if re.sub(r"\([^)]*\)", "", name).islower():  # tout en minuscules hors (Région)
        name = " ".join(w if w.startswith("(") else w[:1].upper() + w[1:] for w in name.split(" "))
    return name or stem


def title_of(filename: str) -> str:
    """Titre sans tags : « Tetris (World) (Rev 1).gb » -> « Tetris »."""
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", Path(filename).stem).strip() or Path(filename).stem


def canonical_name(data: bytes, filename: str, table: dict[str, str]) -> tuple[str, bool]:
    """(nouveau nom de fichier, identifié par CRC ?)."""
    ext = Path(filename).suffix.lower()
    found = identify(data, ext, table)
    if found:
        return found, True
    return clean_name(Path(filename).stem) + ext, False


def read_rom(path: Path, rom_exts: set[str]) -> tuple[bytes, str] | None:
    """Contenu et nom de la ROM (fichier brut, ou zip ne contenant qu'une ROM)."""
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                files = [i for i in zf.infolist() if not i.is_dir()]
                if len(files) != 1 or Path(files[0].filename).suffix.lower() not in rom_exts:
                    return None
                return zf.read(files[0]), Path(files[0].filename).name
        except (zipfile.BadZipFile, OSError):
            return None
    if path.suffix.lower() in rom_exts:
        return path.read_bytes(), path.name
    return None
