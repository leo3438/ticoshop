"""ticoshop : gestion d'un dépôt pour le Store de tico + vitrine publique."""
import hmac
import io
import json
import re
import secrets
from pathlib import Path
from typing import Any

import qrcode
import qrcode.image.svg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile as StarletteUpload
from starlette.middleware.sessions import SessionMiddleware

from . import storage
from .config import Settings
from .db import Database
from .index import CATEGORIES, TYPES, build_index, package_download_url, validate_package

APP_DIR = Path(__file__).parent
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SOURCES = {"upload": "Fichier hébergé ici", "url": "URL externe", "magnet": "Torrent (magnet)"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    max_bytes = settings.max_upload_mb * 1024 * 1024

    app = FastAPI(title="ticoshop", docs_url="/api/docs", redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax", max_age=7 * 86400)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=APP_DIR / "templates")
    templates.env.globals.update(CATEGORIES=CATEGORIES, TYPES=TYPES, SOURCES=SOURCES)
    templates.env.filters["filesize"] = human_size

    # --- utilitaires -----------------------------------------------------
    def base_url(request: Request) -> str:
        return settings.public_url or str(request.base_url).rstrip("/")

    def index_url(request: Request) -> str:
        return f"{base_url(request)}/index.json"

    def render(request: Request, name: str, **ctx: Any) -> Response:
        ctx.setdefault("repo", db.get_repo())
        ctx.setdefault("index_url", index_url(request))
        ctx.setdefault("is_admin", bool(request.session.get("admin")))
        ctx.setdefault("csrf", csrf_token(request))
        return templates.TemplateResponse(request, name, ctx)

    def csrf_token(request: Request) -> str:
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(24)
        return request.session["csrf"]

    def check_csrf(request: Request, token: str) -> None:
        if not hmac.compare_digest(token or "", request.session.get("csrf", "")):
            raise HTTPException(400, "Jeton CSRF invalide, rechargez la page.")

    def admin_page(request: Request) -> None:
        if not request.session.get("admin"):
            raise HTTPException(303, headers={"Location": "/admin/login"})

    def api_auth(request: Request) -> None:
        if request.session.get("admin"):
            return
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip()
        if not settings.api_token or not hmac.compare_digest(token, settings.api_token):
            raise HTTPException(401, "Jeton d'API manquant ou invalide.")

    def get_or_404(pkg_id: str) -> dict[str, Any]:
        pkg = db.get_package(pkg_id)
        if not pkg:
            raise HTTPException(404, "Paquet introuvable.")
        return pkg

    def current_index(request: Request) -> dict[str, Any]:
        return build_index(base_url(request), db.get_repo(), db.list_packages(published_only=True))

    async def store_upload(pkg_id: str, upload: UploadFile) -> None:
        try:
            rel, sha, size = await run_in_threadpool(
                storage.save_package_file, settings.files_dir, pkg_id, upload.filename or "", upload.file, max_bytes
            )
        except storage.UploadTooLarge:
            raise HTTPException(413, f"Fichier trop gros (max {settings.max_upload_mb} Mo).")
        db.set_local_file(pkg_id, rel, Path(rel).name, sha, size)

    async def store_image(upload: UploadFile) -> str:
        try:
            return await run_in_threadpool(storage.save_image, settings.media_dir, upload.filename or "", upload.file)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except storage.UploadTooLarge:
            raise HTTPException(413, "Image trop grosse (max 20 Mo).")

    async def fetch_checksum(pkg: dict[str, Any]) -> dict[str, Any]:
        if pkg["source"] != "url":
            raise HTTPException(400, "Le calcul distant ne s'applique qu'aux paquets « URL externe ».")
        try:
            sha, size = await run_in_threadpool(storage.remote_checksum, pkg["url"], max_bytes)
        except Exception as e:  # réseau, 404, taille…
            raise HTTPException(502, f"Téléchargement impossible : {e}")
        return db.update_package(pkg["id"], {"sha256": sha, "size": size})  # type: ignore[return-value]

    # --- pages publiques -------------------------------------------------
    @app.get("/", include_in_schema=False)
    def storefront(request: Request, q: str = "", category: str = ""):
        packages = db.list_packages(published_only=True)
        if category:
            packages = [p for p in packages if p["category"] == category]
        if q:
            ql = q.lower()
            packages = [p for p in packages if ql in f"{p['name']} {p['tagline']} {' '.join(p['tags'])}".lower()]
        used = sorted({p["category"] for p in db.list_packages(published_only=True)})
        return render(request, "store.html", packages=packages, q=q, category=category, used_categories=used)

    @app.get("/p/{pkg_id}", include_in_schema=False)
    def package_page(request: Request, pkg_id: str):
        pkg = get_or_404(pkg_id)
        if not pkg["published"] and not request.session.get("admin"):
            raise HTTPException(404, "Paquet introuvable.")
        return render(request, "package.html", pkg=pkg, download=package_download_url(base_url(request), pkg))

    @app.get("/add", include_in_schema=False)
    def add_page(request: Request):
        return render(request, "add.html", base=base_url(request))

    @app.get("/index.json", tags=["tico"])
    @app.get("/t", include_in_schema=False)  # alias court, plus rapide à taper sur la Switch
    def index_json(request: Request):
        return JSONResponse(current_index(request), headers={"Cache-Control": "no-cache"})

    @app.get("/qr.svg", include_in_schema=False)
    def qr(request: Request):
        img = qrcode.make(index_url(request), image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return Response(buf.getvalue(), media_type="image/svg+xml")

    @app.get("/add-to-switch.sh", include_in_schema=False)
    def add_script(request: Request):
        script = (APP_DIR.parent / "scripts" / "add-to-switch.sh").read_text()
        script = script.replace("__TICOSHOP_INDEX_URL__", index_url(request))
        return PlainTextResponse(script, media_type="text/x-shellscript")

    @app.get("/files/{pkg_id}/{filename}", include_in_schema=False)
    def download(pkg_id: str, filename: str):
        pkg = get_or_404(pkg_id)
        if not pkg["published"] or pkg["source"] != "upload" or pkg["filename"] != filename:
            raise HTTPException(404)
        path = settings.files_dir / pkg["local_file"]
        if not path.is_file():
            raise HTTPException(404)
        db.count_download(pkg_id)
        return FileResponse(path, filename=filename, media_type="application/octet-stream")

    @app.get("/media/{name}", include_in_schema=False)
    def media(name: str):
        path = settings.media_dir / Path(name).name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    # --- administration (HTML) -------------------------------------------
    @app.get("/admin/login", include_in_schema=False)
    def login_form(request: Request):
        return render(request, "admin/login.html", error="")

    @app.post("/admin/login", include_in_schema=False)
    def login(request: Request, password: str = Form(...), csrf: str = Form("")):
        check_csrf(request, csrf)
        if settings.admin_password and hmac.compare_digest(password, settings.admin_password):
            request.session["admin"] = True
            return RedirectResponse("/admin", 303)
        return render(request, "admin/login.html", error="Mot de passe incorrect.")

    @app.post("/admin/logout", include_in_schema=False)
    def logout(request: Request, csrf: str = Form("")):
        check_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/", 303)

    @app.get("/admin", include_in_schema=False, dependencies=[Depends(admin_page)])
    def dashboard(request: Request):
        packages = db.list_packages()
        problems = {p["id"]: validate_package(p) for p in packages}
        return render(request, "admin/dashboard.html", packages=packages, problems=problems)

    @app.get("/admin/repo", include_in_schema=False, dependencies=[Depends(admin_page)])
    def repo_form(request: Request):
        return render(request, "admin/repo.html", saved=False)

    @app.post("/admin/repo", include_in_schema=False, dependencies=[Depends(admin_page)])
    async def repo_save(request: Request):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        repo = db.get_repo()
        values = {k: str(form.get(k, "")).strip() for k in ("name", "description", "maintainer", "website")}
        for kind in ("icon", "cover"):
            upload = form.get(f"{kind}_file")
            if isinstance(upload, StarletteUpload) and upload.filename:
                storage.delete_image(settings.media_dir, repo[kind])
                values[kind] = await store_image(upload)
            elif form.get(f"{kind}_remove"):
                storage.delete_image(settings.media_dir, repo[kind])
                values[kind] = ""
        db.set_repo(values)
        return render(request, "admin/repo.html", saved=True)

    @app.get("/admin/packages/new", include_in_schema=False, dependencies=[Depends(admin_page)])
    def new_form(request: Request):
        return render(request, "admin/package.html", pkg=None, problems=[], error="")

    @app.post("/admin/packages/new", include_in_schema=False, dependencies=[Depends(admin_page)])
    async def new_package(request: Request):
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        pkg_id = str(form.get("id", "")).strip().lower()
        if not ID_RE.match(pkg_id):
            return render(request, "admin/package.html", pkg=None, problems=[],
                          error="Identifiant invalide : lettres minuscules, chiffres, « . », « _ », « - ».")
        if db.get_package(pkg_id):
            return render(request, "admin/package.html", pkg=None, problems=[], error="Cet identifiant existe déjà.")
        db.create_package(pkg_id, {"name": str(form.get("name") or pkg_id)})
        return await save_form(request, pkg_id, form)

    @app.get("/admin/packages/{pkg_id}", include_in_schema=False, dependencies=[Depends(admin_page)])
    def edit_form(request: Request, pkg_id: str):
        pkg = get_or_404(pkg_id)
        return render(request, "admin/package.html", pkg=pkg, problems=validate_package(pkg), error="")

    @app.post("/admin/packages/{pkg_id}", include_in_schema=False, dependencies=[Depends(admin_page)])
    async def edit_package(request: Request, pkg_id: str):
        get_or_404(pkg_id)
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        return await save_form(request, pkg_id, form)

    async def save_form(request: Request, pkg_id: str, form: Any) -> Response:
        pkg = get_or_404(pkg_id)
        values: dict[str, Any] = {
            k: str(form.get(k, "")).strip()
            for k in ("name", "version", "author", "category", "type", "tagline", "description",
                      "changelog", "source", "url", "magnet", "torrent_file", "install", "sha256")
        }
        values["description"] = str(form.get("description", ""))
        values["changelog"] = str(form.get("changelog", ""))
        values["tags"] = [t.strip() for t in str(form.get("tags", "")).split(",") if t.strip()]
        for flag in ("featured", "published", "extract", "extract_to_folder"):
            values[flag] = bool(form.get(flag))
        if values["source"] != "upload":
            values["filename"] = str(form.get("filename", "")).strip()
            values["size"] = int(str(form.get("size") or 0) or 0)
        try:
            extra = json.loads(str(form.get("extra") or "{}"))
            if not isinstance(extra, dict):
                raise ValueError
            values["extra"] = extra
        except ValueError:
            return render(request, "admin/package.html", pkg=pkg, problems=[],
                          error="Le champ « Champs supplémentaires » doit être un objet JSON.")

        for kind in ("icon", "banner"):
            upload = form.get(f"{kind}_file")
            if isinstance(upload, StarletteUpload) and upload.filename:
                storage.delete_image(settings.media_dir, pkg[kind])
                values[kind] = await store_image(upload)
            elif form.get(f"{kind}_remove"):
                storage.delete_image(settings.media_dir, pkg[kind])
                values[kind] = ""
        shots = [s for s in pkg["screenshots"] if s not in form.getlist("screenshot_remove")]
        for s in set(pkg["screenshots"]) - set(shots):
            storage.delete_image(settings.media_dir, s)
        for upload in form.getlist("screenshot_files"):
            if isinstance(upload, StarletteUpload) and upload.filename:
                shots.append(await store_image(upload))
        values["screenshots"] = shots

        db.update_package(pkg_id, values)
        upload = form.get("package_file")
        if values["source"] == "upload" and isinstance(upload, StarletteUpload) and upload.filename:
            await store_upload(pkg_id, upload)
        if values["source"] == "url" and form.get("compute_checksum"):
            try:
                await fetch_checksum(get_or_404(pkg_id))
            except HTTPException as e:
                pkg = get_or_404(pkg_id)
                return render(request, "admin/package.html", pkg=pkg, problems=validate_package(pkg), error=e.detail)
        return RedirectResponse(f"/admin/packages/{pkg_id}?saved=1", 303)

    @app.post("/admin/packages/{pkg_id}/delete", include_in_schema=False, dependencies=[Depends(admin_page)])
    def delete_package_form(request: Request, pkg_id: str, csrf: str = Form("")):
        check_csrf(request, csrf)
        remove_package(get_or_404(pkg_id))
        return RedirectResponse("/admin", 303)

    def remove_package(pkg: dict[str, Any]) -> None:
        storage.delete_package_files(settings.files_dir, pkg["id"])
        for ref in [pkg["icon"], pkg["banner"], *pkg["screenshots"]]:
            storage.delete_image(settings.media_dir, ref)
        db.delete_package(pkg["id"])

    # --- API JSON (CLI bash, CI…) ----------------------------------------
    api = Depends(api_auth)

    @app.get("/api/repo", tags=["api"], dependencies=[api])
    def api_repo():
        return db.get_repo()

    @app.put("/api/repo", tags=["api"], dependencies=[api])
    def api_repo_update(values: dict[str, Any]):
        db.set_repo({k: v for k, v in values.items() if k not in ("icon", "cover")})
        return db.get_repo()

    @app.post("/api/repo/media", tags=["api"], dependencies=[api])
    async def api_repo_media(kind: str = Form(...), file: UploadFile = File(...)):
        if kind not in ("icon", "cover"):
            raise HTTPException(400, "kind doit valoir icon ou cover.")
        storage.delete_image(settings.media_dir, db.get_repo()[kind])
        db.set_repo({kind: await store_image(file)})
        return db.get_repo()

    @app.get("/api/packages", tags=["api"], dependencies=[api])
    def api_list():
        return db.list_packages()

    @app.get("/api/packages/{pkg_id}", tags=["api"], dependencies=[api])
    def api_get(pkg_id: str):
        pkg = get_or_404(pkg_id)
        return {**pkg, "problems": validate_package(pkg)}

    @app.post("/api/packages", tags=["api"], status_code=201, dependencies=[api])
    def api_create(values: dict[str, Any]):
        pkg_id = str(values.pop("id", "")).strip().lower()
        if not ID_RE.match(pkg_id):
            raise HTTPException(400, "id invalide : [a-z0-9._-], 64 caractères max.")
        if db.get_package(pkg_id):
            raise HTTPException(409, "Ce paquet existe déjà.")
        check_enums(values)
        values.setdefault("name", pkg_id)
        return db.create_package(pkg_id, values)

    @app.patch("/api/packages/{pkg_id}", tags=["api"], dependencies=[api])
    def api_update(pkg_id: str, values: dict[str, Any]):
        get_or_404(pkg_id)
        check_enums(values)
        return db.update_package(pkg_id, values)

    @app.delete("/api/packages/{pkg_id}", tags=["api"], status_code=204, dependencies=[api])
    def api_delete(pkg_id: str):
        remove_package(get_or_404(pkg_id))

    @app.post("/api/packages/{pkg_id}/file", tags=["api"], dependencies=[api])
    async def api_upload(pkg_id: str, file: UploadFile = File(...)):
        get_or_404(pkg_id)
        await store_upload(pkg_id, file)
        return get_or_404(pkg_id)

    @app.post("/api/packages/{pkg_id}/media", tags=["api"], dependencies=[api])
    async def api_media(pkg_id: str, kind: str = Form(...), file: UploadFile = File(...)):
        pkg = get_or_404(pkg_id)
        if kind == "screenshot":
            return db.update_package(pkg_id, {"screenshots": [*pkg["screenshots"], await store_image(file)]})
        if kind not in ("icon", "banner"):
            raise HTTPException(400, "kind doit valoir icon, banner ou screenshot.")
        storage.delete_image(settings.media_dir, pkg[kind])
        return db.update_package(pkg_id, {kind: await store_image(file)})

    @app.post("/api/packages/{pkg_id}/checksum", tags=["api"], dependencies=[api])
    async def api_checksum(pkg_id: str):
        return await fetch_checksum(get_or_404(pkg_id))

    @app.get("/api/validate", tags=["api"], dependencies=[api])
    def api_validate():
        return {p["id"]: validate_package(p) for p in db.list_packages() if p["published"]}

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 303:
            return RedirectResponse(exc.headers["Location"], 303)
        if request.url.path.startswith("/api/") or request.url.path == "/index.json":
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        ctx = {"status": exc.status_code, "detail": exc.detail, "repo": db.get_repo(),
               "is_admin": bool(request.session.get("admin")), "csrf": csrf_token(request)}
        return templates.TemplateResponse(request, "error.html", ctx, status_code=exc.status_code)

    app.state.settings = settings
    app.state.db = db
    return app


def check_enums(values: dict[str, Any]) -> None:
    if "category" in values and values["category"] not in CATEGORIES:
        raise HTTPException(400, f"category doit être parmi : {', '.join(CATEGORIES)}")
    if "type" in values and values["type"] not in TYPES:
        raise HTTPException(400, f"type doit être parmi : {', '.join(TYPES)}")
    if "source" in values and values["source"] not in SOURCES:
        raise HTTPException(400, f"source doit être parmi : {', '.join(SOURCES)}")


def human_size(n: int) -> str:
    size = float(n or 0)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} Go"

