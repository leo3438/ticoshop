#!/usr/bin/env python3
"""Renomme des ROM au nom officiel No-Intro, pour que tico trouve leurs jaquettes
(SteamGridDB, libretro) et leurs titres.

Chaque ROM est identifiée par son empreinte CRC32 dans les bases No-Intro de
libretro-database (téléchargées une fois, puis gardées en cache). Une ROM
inconnue garde un nom nettoyé : « super_mario_land_(E)_[!] » -> « Super Mario Land (Europe) ».

    ./scripts/rename-roms.py /media/$USER/SWITCH            # carte SD : tico/roms/<console>/
    ./scripts/rename-roms.py ~/roms/gba --console gba       # un dossier d'une console
    ./scripts/rename-roms.py /media/$USER/SWITCH --apply    # renomme vraiment

Sans --apply, rien n'est modifié : la liste des changements est seulement affichée.
Un .zip ne contenant qu'une ROM est renommé d'après elle (son contenu reste intact).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import romnames  # noqa: E402
from app.covers import EXTENSIONS  # noqa: E402

ROM_EXTS = set(EXTENSIONS)


def folders(root: Path, console: str | None) -> list[tuple[Path, str]]:
    if console:
        return [(root, console)]
    roms = root / "tico" / "roms"
    if roms.is_dir():
        return [(d, d.name) for d in sorted(roms.iterdir()) if d.is_dir()]
    if romnames.system_for(root.name):
        return [(root, root.name)]
    sys.exit(f"Impossible de deviner la console de {root} : utilisez --console (gb, gba, snes…), "
             "ou donnez la racine de la carte SD.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dossier", type=Path, help="racine de la carte SD, ou dossier de ROM d'une console")
    ap.add_argument("--console", help="slug tico de la console (gb, gbc, gba, nes, snes, n64, nds, genesis…)")
    ap.add_argument("--apply", action="store_true", help="renommer réellement les fichiers")
    ap.add_argument("--dats", type=Path, default=Path.home() / ".cache" / "ticoshop-dats",
                    help="cache des bases No-Intro (défaut : ~/.cache/ticoshop-dats)")
    ap.add_argument("--dats-url", default=romnames.DATS_URL, help="miroir de libretro-database/metadat/no-intro")
    args = ap.parse_args()

    if not args.dossier.is_dir():
        sys.exit(f"Dossier introuvable : {args.dossier}")
    total = renamed = identified = 0
    for folder, console in folders(args.dossier, args.console):
        if not romnames.system_for(console):
            print(f"· {folder.name} : console sans base No-Intro (CD, arcade…), ignorée")
            continue
        table = romnames.load_dat(console, args.dats, args.dats_url)
        if not table:
            print(f"⚠ {folder.name} : base No-Intro indisponible (réseau ?), noms seulement nettoyés")
        for path in sorted(p for p in folder.iterdir() if p.is_file()):
            rom = romnames.read_rom(path, ROM_EXTS)
            if not rom:
                continue
            total += 1
            data, inner = rom
            new_name, ok = romnames.canonical_name(data, inner, table)
            identified += ok
            target = path.with_name(Path(new_name).stem + path.suffix) if path.suffix.lower() == ".zip" \
                else path.with_name(new_name)
            if target.name == path.name:
                continue
            mark = "✓ No-Intro" if ok else "~ nettoyé "
            if target.exists():
                print(f"  ! {path.name}  ->  {target.name}  (existe déjà, ignoré)")
                continue
            print(f"  {mark}  {folder.name}/{path.name}  ->  {target.name}")
            if args.apply:
                path.rename(target)
            renamed += 1
    verb = "renommées" if args.apply else "à renommer (relancez avec --apply)"
    print(f"\n{total} ROM, {identified} reconnues par CRC, {renamed} {verb}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
