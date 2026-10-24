"""État partagé par les routes : configuration, base, templates et utilitaires."""
import hmac
import secrets
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from . import consolepacks, covers, notify, storage
from .config import Settings
from .db import Database
from .i18n import LANGS, pick_lang, translator
from .index import CATEGORIES, TYPES, build_index, category_of
from .security import LoginLimiter, verify_password

APP_DIR = Path(__file__).parent
SOURCES = {"upload": "Fichier hébergé ici", "url": "URL externe", "magnet": "Torrent (magnet)"}


def human_size(n: int) -> str:
    size = float(n or 0)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} Go"


class Context:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_dirs()
        self.db = Database(settings.db_path, settings.files_dir, settings.keep_versions)
        self.max_bytes = settings.max_upload_mb * 1024 * 1024
        self.limiter = LoginLimiter(settings.login_max_attempts, settings.login_window_minutes * 60)
        self.templates = Jinja2Templates(directory=APP_DIR / "templates")
        self.templates.env.globals.update(CATEGORIES=CATEGORIES, TYPES=TYPES, SOURCES=SOURCES, LANGS=LANGS,
                                          CONSOLES=covers.CONSOLES)
        self.templates.env.filters["filesize"] = human_size
        if settings.webhook_url:
            self.db.listeners.append(self._notify)

    # --- URL ---------------------------------------------------------------
    def base_url(self, request: Request | None = None) -> str:
        if self.settings.public_url or request is None:
            return self.settings.public_url
        return str(request.base_url).rstrip("/")

    def index_url(self, request: Request) -> str:
        return f"{self.base_url(request)}/index.json"

    def current_index(self, request: Request) -> dict[str, Any]:
        repo = self.db.get_repo()
        packages = self.db.list_packages(published_only=True)
        releases = None
        if repo.get("publish_versions"):
            releases = {p["id"]: self.db.list_releases(p["id"]) for p in packages}
        return build_index(self.base_url(request), repo, packages, releases)

    # --- rendu -------------------------------------------------------------
    def render(self, request: Request, name: str, public: bool = False, status_code: int = 200, **ctx: Any) -> Response:
        lang = "fr"
        if public:
            lang = pick_lang(request.query_params.get("lang"), request.cookies.get("lang"),
                             request.headers.get("accept-language"))
        ctx.setdefault("repo", self.db.get_repo())
        ctx.setdefault("index_url", self.index_url(request))
        ctx.setdefault("base", self.base_url(request))
        ctx.setdefault("admin_user", request.session.get("admin") or "")
        ctx.setdefault("is_admin", bool(request.session.get("admin")))
        ctx.setdefault("csrf", self.csrf_token(request))
        ctx.setdefault("flash", request.session.pop("flash", ""))
        ctx.update(lang=lang, t=translator(lang), public=public)
        response = self.templates.TemplateResponse(request, name, ctx, status_code=status_code)
        if public and request.query_params.get("lang") in LANGS:
            response.set_cookie("lang", lang, max_age=365 * 86400, samesite="lax")
        return response

    # --- sécurité ----------------------------------------------------------
    @staticmethod
    def csrf_token(request: Request) -> str:
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(24)
        return request.session["csrf"]

    @staticmethod
    def check_csrf(request: Request, token: str) -> None:
        if not hmac.compare_digest(token or "", request.session.get("csrf", "")):
            raise HTTPException(400, "Jeton CSRF invalide, rechargez la page.")

    def authenticate(self, username: str, password: str) -> bool:
        stored = self.db.get_password_hash(username)
        if stored:
            return verify_password(password, stored)
        # Compte « admin » de secours défini dans .env.
        return (username == "admin" and bool(self.settings.admin_password)
                and hmac.compare_digest(password, self.settings.admin_password))

    def admin_page(self, request: Request) -> None:
        if not request.session.get("admin"):
            raise HTTPException(303, headers={"Location": "/admin/login"})

    def api_auth(self, request: Request) -> None:
        if request.session.get("admin"):
            return
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not self.settings.api_token or not hmac.compare_digest(token, self.settings.api_token):
            raise HTTPException(401, "Jeton d'API manquant ou invalide.")

    # --- paquets -----------------------------------------------------------
    def get_or_404(self, pkg_id: str) -> dict[str, Any]:
        pkg = self.db.get_package(pkg_id)
        if not pkg:
            raise HTTPException(404, "Paquet introuvable.")
        return pkg

    async def store_upload(self, pkg_id: str, upload: UploadFile,
                           values: dict[str, Any] | None = None) -> dict[str, Any]:
        """Enregistre le fichier (et « values » dans la même mise à jour), puis l'adapte à tico :

        - module tico : pré-remplit le paquet d'après son module.json ;
        - ROM : l'emballe dans un zip « tico/roms/<console>/<nom> » (tico 0.8 masque
          les paquets « type: rom » des dépôts communautaires mais installe ce zip) ;
        - console pack / soundpack : convertit l'archive (zip, rar, 7z, même au format
          « à coller à la racine de la SD ») vers les dossiers de tico 0.8.
        Renvoie {"module", "rom_path", "pack"} (ce qui a été fait).
        """
        try:
            rel, sha, size = await run_in_threadpool(
                storage.save_package_file, self.settings.files_dir, pkg_id, upload.filename or "",
                upload.file, self.max_bytes,
            )
        except storage.UploadTooLarge:
            raise HTTPException(413, f"Fichier trop gros (max {self.settings.max_upload_mb} Mo).")
        values = dict(values or {})
        pkg = {**self.get_or_404(pkg_id), **values}
        info: dict[str, Any] = {"module": None, "rom_path": "", "pack": None}
        category = category_of(pkg)
        if (pkg["type"] == "rom" or category == "game") and not pkg.get("keep_archive"):
            _, ext = covers.rom_name(rel, self.settings.files_dir / rel)
            console = pkg.get("console") or covers.guess_console(ext)
            if console:
                res = await run_in_threadpool(storage.wrap_rom, self.settings.files_dir, pkg_id, rel, console,
                                              set(covers.EXTENSIONS))
                if res:
                    rel, sha, size, rom = res
                    info["rom_path"] = f"tico/roms/{console}/{rom}"
                    values.update(type="zip", console=console, install="", keep_archive=False,
                                  extract_to_folder=False)
        elif category in ("consolepack", "soundpack") and Path(rel).suffix.lower() in consolepacks.ARCHIVE_EXTS:
            converted = await run_in_threadpool(self._convert_pack, pkg_id, rel, category)
            if converted:
                rel, sha, size, pack_info, previews, sounds = converted
                info["pack"] = pack_info
                folder = "consolepacks" if category == "consolepack" else "soundpacks"
                values.update(type="zip", install=f"tico/theme/{folder}/{pkg_id}", keep_archive=False,
                              extract_to_folder=False)
                self._add_previews(pkg, values, previews)
        self.db.set_local_file(pkg_id, rel, Path(rel).name, sha, size, values)
        if info["pack"] and info["pack"].get("soundpacks"):
            info["pack"]["soundpack_packages"] = self._create_soundpacks(pkg_id, sounds)
        if self.settings.auto_covers:
            await self.auto_covers(pkg_id)
        if not info["rom_path"] and not info["pack"] and rel.lower().endswith(".zip"):
            info["module"] = await run_in_threadpool(storage.inspect_module, self.settings.files_dir / rel)
        if info["module"]:
            self.apply_module(pkg_id, info["module"])
        return info

    def _convert_pack(self, pkg_id: str, rel: str, category: str):
        """Réorganise l'archive envoyée ; None si elle ne contient rien de reconnu."""
        src = self.settings.files_dir / rel
        try:
            pack, tmp = consolepacks.open_pack(src, self.max_bytes)
        except consolepacks.PackError:
            return None  # gardé tel quel : la validation signalera le problème
        try:
            if category == "consolepack":
                files = pack.files
            else:  # soundpack : le premier jeu de sons trouvé, à la racine du dossier
                files = next(iter(pack.soundpacks.values()), {})
            if not files:
                return None
            token = uuid.uuid4().hex[:12]
            out = self.settings.files_dir / pkg_id / token / f"{pkg_id}.zip"
            consolepacks.write_zip(files, out)
            sha, size = storage.file_digest(out)
            previews = [p.read_bytes() for p in pack.previews[:8] if p.stat().st_size < 20 * 1024 * 1024]
            preview_exts = [p.suffix.lower() for p in pack.previews[:8] if p.stat().st_size < 20 * 1024 * 1024]
            sounds = {name: {k: v.read_bytes() for k, v in fs.items()} for name, fs in pack.soundpacks.items()} \
                if category == "consolepack" else {}
            pack_info = {
                "consoles": pack.consoles(),
                "counts": {k: pack.count(k) for k in consolepacks.KINDS},
                "colors": "colors.json" in pack.files,
                "soundpacks": list(sounds),
                "skipped": pack.skipped[:20],
            }
        finally:
            tmp.cleanup()
        src.unlink(missing_ok=True)
        if src.parent.exists() and not any(src.parent.iterdir()):
            src.parent.rmdir()
        return (f"{pkg_id}/{token}/{out.name}", sha, size, pack_info,
                list(zip(previews, preview_exts)), sounds)

    def _add_previews(self, pkg: dict[str, Any], values: dict[str, Any], previews: list) -> None:
        """Les images d'aperçu de l'archive deviennent bannière / captures si le paquet n'en a pas."""
        if not previews:
            return
        media = self.settings.media_dir
        shots = list(pkg.get("screenshots") or [])
        if not pkg.get("banner"):
            values["banner"] = storage.save_image_bytes(media, previews[0][0], previews[0][1])
        if not shots:
            values["screenshots"] = [storage.save_image_bytes(media, data, ext) for data, ext in previews[:8]]

    def _create_soundpacks(self, pkg_id: str, sounds: dict[str, dict[str, bytes]]) -> list[str]:
        """Un soundpack livré avec un console pack devient son propre paquet (dossier différent)."""
        parent = self.get_or_404(pkg_id)
        created = []
        for i, (name, files) in enumerate(sounds.items()):
            sid = f"{pkg_id}-sons" if len(sounds) == 1 else f"{pkg_id}-sons-{i + 1}"
            if not self.db.get_package(sid):
                self.db.create_package(sid, {
                    "name": f"{parent['name']} (sons)", "category": "soundpack", "type": "zip",
                    "version": parent["version"], "author": parent["author"], "published": False,
                    "description": f"Soundpack « {name} » livré avec {parent['name']}.",
                    "icon": parent["icon"], "banner": parent["banner"],
                })
            token = uuid.uuid4().hex[:12]
            out = self.settings.files_dir / sid / token / f"{sid}.zip"
            out.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                for arcname, data in sorted(files.items()):
                    zf.writestr(arcname, data)
            sha, size = storage.file_digest(out)
            self.db.set_local_file(sid, f"{sid}/{token}/{out.name}", out.name, sha, size, {
                "install": f"tico/theme/soundpacks/{sid}", "published": parent["published"],
                "version": parent["version"],
            })
            created.append(sid)
        return created

    async def auto_covers(self, pkg_id: str) -> None:
        """À l'envoi d'une ROM : devine la console et ajoute la jaquette si le paquet n'en a pas."""
        pkg = self.get_or_404(pkg_id)
        if pkg["type"] != "rom" and pkg["category"] != "game":
            return
        name, ext = covers.rom_name(pkg["filename"], self.settings.files_dir / pkg["local_file"])
        console = pkg["console"] or covers.guess_console(ext)
        if console and not pkg["console"]:
            self.db.update_package(pkg_id, {"console": console})
        if console and not pkg["icon"]:
            try:
                await self.apply_covers(pkg_id, name=name, console=console)
            except HTTPException:
                pass  # jaquette facultative : l'envoi du fichier ne doit pas échouer pour ça

    async def apply_covers(self, pkg_id: str, name: str = "", console: str = "", force: bool = False) -> dict[str, Any]:
        """Cherche les images libretro et les ajoute au paquet.

        Renvoie {"found": nom trouvé ou "", "console", "tried": nom cherché, "added": [types]}.
        """
        pkg = self.get_or_404(pkg_id)
        console = console or pkg["console"]
        if not name and pkg["filename"]:
            local = self.settings.files_dir / pkg["local_file"] if pkg["local_file"] else None
            name = covers.rom_name(pkg["filename"], local)[0]
        name = name or pkg["name"]
        if console not in covers.CONSOLES:
            raise HTTPException(400, "Choisissez la console du jeu pour chercher sa jaquette.")
        try:
            found, images = await run_in_threadpool(covers.find_covers, self.settings.covers_url, console, name)
        except covers.CoverServerError as e:
            raise HTTPException(502, f"Serveur de jaquettes injoignable ({self.settings.covers_url}) : {e}")
        added = []
        values: dict[str, Any] = {"console": console}
        media = self.settings.media_dir
        if "boxart" in images and (force or not pkg["icon"]):
            storage.delete_image(media, pkg["icon"])
            values["icon"] = storage.save_image_bytes(media, images["boxart"])
            added.append("jaquette")
        if "title" in images and (force or not pkg["banner"]):
            storage.delete_image(media, pkg["banner"])
            values["banner"] = storage.save_image_bytes(media, images["title"])
            added.append("écran titre")
        if "snap" in images and (force or not pkg["screenshots"]):
            values["screenshots"] = [*pkg["screenshots"], storage.save_image_bytes(media, images["snap"])]
            added.append("capture")
        self.db.update_package(pkg_id, values)
        return {"found": found, "console": console, "tried": name, "added": added}

    def apply_module(self, pkg_id: str, module: dict[str, Any]) -> None:
        pkg = self.get_or_404(pkg_id)
        # Comme le dépôt officiel : catégorie « app », tags « emulator » + consoles.
        values: dict[str, Any] = {"type": "module", "category": "app"}
        if not pkg["name"] or pkg["name"] == pkg_id:
            values["name"] = module["name"]
        consoles = [s["slug"] for s in module["systems"] if s["slug"]]
        values["tags"] = list(dict.fromkeys(["emulator", *pkg["tags"], *consoles]))
        # Pas de dossier « install » : tico place lui-même les modules dans
        # tico/modules, un chemin explicite serait refusé (dossier protégé).
        self.db.update_package(pkg_id, values)

    async def store_image(self, upload: UploadFile) -> str:
        try:
            return await run_in_threadpool(storage.save_image, self.settings.media_dir, upload.filename or "", upload.file)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except storage.UploadTooLarge:
            raise HTTPException(413, "Image trop grosse (max 20 Mo).")

    async def fetch_checksum(self, pkg: dict[str, Any]) -> dict[str, Any]:
        if pkg["source"] != "url":
            raise HTTPException(400, "Le calcul distant ne s'applique qu'aux paquets « URL externe ».")
        try:
            sha, size = await run_in_threadpool(storage.remote_checksum, pkg["url"], self.max_bytes)
        except Exception as e:  # réseau, 404, taille…
            raise HTTPException(502, f"Téléchargement impossible : {e}")
        return self.db.update_package(pkg["id"], {"sha256": sha, "size": size})  # type: ignore[return-value]

    def remove_package(self, pkg: dict[str, Any]) -> None:
        storage.delete_package_files(self.settings.files_dir, pkg["id"])
        for ref in [pkg["icon"], pkg["banner"], *pkg["screenshots"]]:
            storage.delete_image(self.settings.media_dir, ref)
        self.db.delete_package(pkg["id"])

    # --- notifications -----------------------------------------------------
    def _notify(self, old: dict[str, Any] | None, new: dict[str, Any]) -> None:
        event = notify.event_for(old, new)
        if event:
            repo = self.db.get_repo()
            body = notify.payload(self.settings.webhook_url, event, new, self.settings.public_url, repo["name"])
            notify.send(self.settings.webhook_url, body)
