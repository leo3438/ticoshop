"""État partagé par les routes : configuration, base, templates et utilitaires."""
import hmac
import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from . import covers, notify, storage
from .config import Settings
from .db import Database
from .i18n import LANGS, pick_lang, translator
from .index import CATEGORIES, TYPES, build_index
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
                           values: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Enregistre le fichier (et « values » dans la même mise à jour) ;
        si c'est un module tico, pré-remplit le paquet.

        Renvoie les infos du module.json trouvé, ou None.
        """
        try:
            rel, sha, size = await run_in_threadpool(
                storage.save_package_file, self.settings.files_dir, pkg_id, upload.filename or "",
                upload.file, self.max_bytes,
            )
        except storage.UploadTooLarge:
            raise HTTPException(413, f"Fichier trop gros (max {self.settings.max_upload_mb} Mo).")
        self.db.set_local_file(pkg_id, rel, Path(rel).name, sha, size, values)
        if self.settings.auto_covers:
            await self.auto_covers(pkg_id)
        module = None
        if rel.lower().endswith(".zip"):
            module = await run_in_threadpool(storage.inspect_module, self.settings.files_dir / rel)
        if module:
            self.apply_module(pkg_id, module)
        return module

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
        values: dict[str, Any] = {"type": "module", "category": "emulator"}
        if not pkg["name"] or pkg["name"] == pkg_id:
            values["name"] = module["name"]
        consoles = [s["short_title"] or s["title"] for s in module["systems"] if s["short_title"] or s["title"]]
        values["tags"] = list(dict.fromkeys([*pkg["tags"], *consoles]))
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
