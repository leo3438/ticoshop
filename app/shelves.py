"""Rayons par console : filtres de la vitrine et dépôts tico découpés.

tico 0.8 n'affiche que les 400 premiers paquets d'un dépôt (constaté sur Switch).
Le shop publie donc aussi un dépôt par rayon (une console, ou « apps » pour le
reste), découpé en parties de 400 si besoin : /r/<rayon>/index.json.
"""
from typing import Any

from . import covers

TICO_MAX_PACKAGES = 400
APPS = "apps"

SORTS = ("console", "name", "recent", "popular")


def console_label(slug: str) -> str:
    return covers.CONSOLES[slug][0] if slug in covers.CONSOLES else slug.upper()


def consoles_of(pkg: dict[str, Any]) -> list[str]:
    """Consoles d'un paquet : celle du jeu, sinon les tags de console (ex. module émulateur)."""
    found = [pkg["console"]] if pkg.get("console") else []
    return found + [t for t in pkg["tags"] if t in covers.CONSOLES and t not in found]


def main_console(pkg: dict[str, Any]) -> str:
    return (consoles_of(pkg) or [""])[0]


def console_counts(packages: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    """[(slug, nom, nombre de paquets)], dans l'ordre de la liste des consoles."""
    counts: dict[str, int] = {}
    for p in packages:
        for slug in consoles_of(p):
            counts[slug] = counts.get(slug, 0) + 1
    order = list(covers.CONSOLES)
    ranked = sorted(counts, key=lambda s: order.index(s) if s in order else len(order))
    return [(s, console_label(s), counts[s]) for s in ranked]


def sort_packages(packages: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    by_name = sorted(packages, key=lambda p: p["name"].casefold())
    if sort == "recent":
        return sorted(by_name, key=lambda p: p["updated_at"], reverse=True)
    if sort == "popular":
        return sorted(by_name, key=lambda p: p["downloads"], reverse=True)
    return sorted(by_name, key=lambda p: not p["featured"])


def group_by_console(packages: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Un rayon par console (jeux uniquement), puis le reste ; slug "" = autres paquets."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in packages:
        slug = main_console(p) if p["category"] == "game" else ""
        groups.setdefault(slug, []).append(p)
    order = list(covers.CONSOLES)
    keys = sorted((k for k in groups if k), key=lambda s: order.index(s) if s in order else len(order))
    return [(k, groups[k]) for k in keys] + ([("", groups[""])] if "" in groups else [])


def shelf_of(pkg: dict[str, Any]) -> str:
    """Rayon d'un paquet : la console d'un jeu, sinon « apps » (applications, émulateurs, packs…)."""
    return (main_console(pkg) if pkg["category"] == "game" else "") or APPS


def split_shelves(packages: list[dict[str, Any]], limit: int = 0) -> dict[str, list[dict[str, Any]]]:
    """{rayon: paquets} ; un rayon trop grand devient « gba », « gba.2 »… (ordre alphabétique)."""
    limit = limit or TICO_MAX_PACKAGES
    by_shelf: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(packages, key=lambda p: p["name"].casefold()):
        by_shelf.setdefault(shelf_of(p), []).append(p)
    order = [*covers.CONSOLES, APPS]
    out: dict[str, list[dict[str, Any]]] = {}
    for slug in sorted(by_shelf, key=lambda s: order.index(s) if s in order else len(order)):
        items = by_shelf[slug]
        for n, start in enumerate(range(0, len(items), limit), 1):
            out[slug if n == 1 else f"{slug}.{n}"] = items[start:start + limit]
    return out


def shelf_label(shelf: str, apps_label: str = "Applications et autres") -> str:
    base, _, part = shelf.partition(".")
    label = apps_label if base == APPS else console_label(base)
    return f"{label} ({part})" if part else label
