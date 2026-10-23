"""Traductions de la vitrine publique (l'admin reste en français)."""
from typing import Callable

LANGS = {"fr": "Français", "en": "English"}
DEFAULT = "fr"

STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "nav.catalog": "Catalogue",
        "nav.add": "Ajouter à tico",
        "nav.admin": "Admin",
        "nav.logout": "Déconnexion",
        "footer.repo_for": "Dépôt pour le Store de",
        "footer.rss": "Nouveautés (RSS)",
        "copy": "Copier",
        "copied": "Copié ✓",
        "store.how": "Comment l'ajouter ?",
        "store.search": "Rechercher…",
        "store.all": "Toutes les catégories",
        "store.empty": "Aucun paquet pour l'instant.",
        "pkg.install": "Installer depuis tico",
        "pkg.download": "Télécharger le fichier",
        "pkg.edit": "Modifier",
        "pkg.by": "par",
        "pkg.on_switch": "Sur la Switch : tico › Store › recherchez « {name} ».",
        "pkg.about": "À propos",
        "pkg.screenshots": "Captures",
        "pkg.whats_new": "Nouveautés",
        "pkg.versions": "Versions précédentes",
        "pkg.plays": "Consoles",
        "add.title": "Ajouter « {name} » à tico",
        "add.requires": "Il faut <strong>tico 0.8.0 ou plus récent</strong> sur une Switch sous Atmosphère, connectée à Internet.",
        "add.short": "Plus court à taper :",
        "add.copy_small": "copier",
        "add.switch": "① Depuis la Switch",
        "add.step1": "Ouvrez <strong>tico</strong> et allez dans le <strong>Store</strong>.",
        "add.step2": "Onglet <strong>Sources</strong> (Repositories) › <strong>Ajouter un dépôt</strong>.",
        "add.step3": "Tapez l'adresse ci-dessus puis validez.",
        "add.step4": "Les paquets apparaissent dans <em>Discover</em>, <em>Apps</em> et <em>Games</em>.",
        "add.community": "Le dépôt sera marqué « Communauté ». Vous pouvez le passer en « Trusted » dans Sources pour installer sans confirmation.",
        "add.qr": "QR code",
        "add.qr_help": "Scannez-le avec votre téléphone pour avoir l'adresse sous les yeux.",
        "add.pc": "② Depuis un PC (carte SD)",
        "add.pc_intro": "Insérez la carte SD de la Switch (ou montez-la via USB avec Hekate / DBI), puis :",
        "add.pc_help": "Le script sauvegarde la configuration existante de tico avant de la modifier (<code>.bak</code>). Exemples de chemin : <code>/media/$USER/SWITCH</code> (Linux), <code>/Volumes/SWITCH</code> (macOS), <code>/e</code> sous Git Bash (Windows).",
        "error.title": "Erreur {status}",
        "error.back": "← Retour",
        "cat.emulator": "Émulateur", "cat.app": "Application", "cat.homebrew": "Homebrew",
        "cat.game": "Jeu", "cat.port": "Port", "cat.fangame": "Fan game", "cat.soundpack": "Soundpack",
        "cat.consolepack": "Console pack", "cat.tool": "Outil", "cat.other": "Autre",
    },
    "en": {
        "nav.catalog": "Catalog",
        "nav.add": "Add to tico",
        "nav.admin": "Admin",
        "nav.logout": "Log out",
        "footer.repo_for": "Repository for the Store of",
        "footer.rss": "What's new (RSS)",
        "copy": "Copy",
        "copied": "Copied ✓",
        "store.how": "How to add it?",
        "store.search": "Search…",
        "store.all": "All categories",
        "store.empty": "No packages yet.",
        "pkg.install": "Install from tico",
        "pkg.download": "Download file",
        "pkg.edit": "Edit",
        "pkg.by": "by",
        "pkg.on_switch": "On the Switch: tico › Store › search for “{name}”.",
        "pkg.about": "About",
        "pkg.screenshots": "Screenshots",
        "pkg.whats_new": "What's new",
        "pkg.versions": "Previous versions",
        "pkg.plays": "Consoles",
        "add.title": "Add “{name}” to tico",
        "add.requires": "You need <strong>tico 0.8.0 or newer</strong> on a Switch running Atmosphère, connected to the Internet.",
        "add.short": "Shorter to type:",
        "add.copy_small": "copy",
        "add.switch": "① From the Switch",
        "add.step1": "Open <strong>tico</strong> and go to the <strong>Store</strong>.",
        "add.step2": "<strong>Sources</strong> (Repositories) tab › <strong>Add repository</strong>.",
        "add.step3": "Type the address above and confirm.",
        "add.step4": "Packages show up in <em>Discover</em>, <em>Apps</em> and <em>Games</em>.",
        "add.community": "The repository is marked “Community”. You can mark it “Trusted” in Sources to install without confirmation.",
        "add.qr": "QR code",
        "add.qr_help": "Scan it with your phone to keep the address in front of you.",
        "add.pc": "② From a computer (SD card)",
        "add.pc_intro": "Insert the Switch SD card (or mount it over USB with Hekate / DBI), then run:",
        "add.pc_help": "The script backs up tico's existing configuration before changing it (<code>.bak</code>). Example paths: <code>/media/$USER/SWITCH</code> (Linux), <code>/Volumes/SWITCH</code> (macOS), <code>/e</code> in Git Bash (Windows).",
        "error.title": "Error {status}",
        "error.back": "← Back",
        "cat.emulator": "Emulator", "cat.app": "App", "cat.homebrew": "Homebrew",
        "cat.game": "Game", "cat.port": "Port", "cat.fangame": "Fan game", "cat.soundpack": "Soundpack",
        "cat.consolepack": "Console pack", "cat.tool": "Tool", "cat.other": "Other",
    },
}


def pick_lang(query: str | None, cookie: str | None, accept: str | None) -> str:
    for candidate in (query, cookie):
        if candidate in LANGS:
            return candidate  # type: ignore[return-value]
    for part in (accept or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGS:
            return code
    return DEFAULT


def translator(lang: str) -> Callable[..., str]:
    table = STRINGS.get(lang, STRINGS[DEFAULT])

    def t(key: str, **kwargs: object) -> str:
        text = table.get(key) or STRINGS[DEFAULT].get(key, key)
        return text.format(**kwargs) if kwargs else text

    return t
