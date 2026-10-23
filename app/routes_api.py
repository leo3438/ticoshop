"""API JSON (CLI bash, CI…). Authentification : Authorization: Bearer <TICOSHOP_API_TOKEN>."""
import re
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from . import backup, covers, github, storage
from .context import SOURCES, Context
from .index import CATEGORIES, TYPES, validate_package

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def check_enums(values: dict[str, Any]) -> None:
    if "category" in values and values["category"] not in CATEGORIES:
        raise HTTPException(400, f"category doit être parmi : {', '.join(CATEGORIES)}")
    if "type" in values and values["type"] not in TYPES:
        raise HTTPException(400, f"type doit être parmi : {', '.join(TYPES)}")
    if "source" in values and values["source"] not in SOURCES:
        raise HTTPException(400, f"source doit être parmi : {', '.join(SOURCES)}")
    if values.get("console") and values["console"] not in covers.CONSOLES:
        raise HTTPException(400, f"console doit être parmi : {', '.join(covers.CONSOLES)}")
    if values.get("github_repo") and not github.REPO_RE.match(values["github_repo"]):
        raise HTTPException(400, "github_repo attendu sous la forme propriétaire/nom.")


def register(app: FastAPI, ctx: Context) -> None:
    db = ctx.db
    settings = ctx.settings
    api = [Depends(ctx.api_auth)]

    @app.get("/api/repo", tags=["api"], dependencies=api)
    def api_repo():
        return db.get_repo()

    @app.put("/api/repo", tags=["api"], dependencies=api)
    def api_repo_update(values: dict[str, Any]):
        db.set_repo({k: v for k, v in values.items() if k not in ("icon", "cover")})
        return db.get_repo()

    @app.post("/api/repo/media", tags=["api"], dependencies=api)
    async def api_repo_media(kind: str = Form(...), file: UploadFile = File(...)):
        if kind not in ("icon", "cover"):
            raise HTTPException(400, "kind doit valoir icon ou cover.")
        storage.delete_image(settings.media_dir, db.get_repo()[kind])
        db.set_repo({kind: await ctx.store_image(file)})
        return db.get_repo()

    @app.get("/api/packages", tags=["api"], dependencies=api)
    def api_list():
        return db.list_packages()

    @app.get("/api/packages/{pkg_id}", tags=["api"], dependencies=api)
    def api_get(pkg_id: str):
        pkg = ctx.get_or_404(pkg_id)
        return {**pkg, "problems": validate_package(pkg)}

    @app.post("/api/packages", tags=["api"], status_code=201, dependencies=api)
    def api_create(values: dict[str, Any]):
        pkg_id = str(values.pop("id", "")).strip().lower()
        if not ID_RE.match(pkg_id):
            raise HTTPException(400, "id invalide : [a-z0-9._-], 64 caractères max.")
        if db.get_package(pkg_id):
            raise HTTPException(409, "Ce paquet existe déjà.")
        check_enums(values)
        values.setdefault("name", pkg_id)
        return db.create_package(pkg_id, values)

    @app.patch("/api/packages/{pkg_id}", tags=["api"], dependencies=api)
    def api_update(pkg_id: str, values: dict[str, Any]):
        ctx.get_or_404(pkg_id)
        check_enums(values)
        return db.update_package(pkg_id, values)

    @app.delete("/api/packages/{pkg_id}", tags=["api"], status_code=204, dependencies=api)
    def api_delete(pkg_id: str):
        ctx.remove_package(ctx.get_or_404(pkg_id))

    @app.post("/api/packages/{pkg_id}/file", tags=["api"], dependencies=api)
    async def api_upload(pkg_id: str, file: UploadFile = File(...), version: str | None = Form(None),
                         changelog: str | None = Form(None)):
        """version / changelog (facultatifs) sont appliqués avec le fichier, en une fois."""
        ctx.get_or_404(pkg_id)
        values = {k: v for k, v in (("version", version), ("changelog", changelog)) if v is not None}
        module = await ctx.store_upload(pkg_id, file, values)
        return {**ctx.get_or_404(pkg_id), "module": module}

    @app.post("/api/packages/{pkg_id}/media", tags=["api"], dependencies=api)
    async def api_media(pkg_id: str, kind: str = Form(...), file: UploadFile = File(...)):
        pkg = ctx.get_or_404(pkg_id)
        if kind == "screenshot":
            return db.update_package(pkg_id, {"screenshots": [*pkg["screenshots"], await ctx.store_image(file)]})
        if kind not in ("icon", "banner"):
            raise HTTPException(400, "kind doit valoir icon, banner ou screenshot.")
        storage.delete_image(settings.media_dir, pkg[kind])
        return db.update_package(pkg_id, {kind: await ctx.store_image(file)})

    @app.post("/api/packages/{pkg_id}/checksum", tags=["api"], dependencies=api)
    async def api_checksum(pkg_id: str):
        return await ctx.fetch_checksum(ctx.get_or_404(pkg_id))

    @app.post("/api/packages/{pkg_id}/covers", tags=["api"], dependencies=api)
    async def api_covers(pkg_id: str, values: dict[str, Any] | None = None):
        """Cherche jaquette, écran titre et capture (libretro). Corps facultatif : {name, console, force}."""
        values = values or {}
        check_enums(values)
        res = await ctx.apply_covers(pkg_id, name=str(values.get("name") or ""),
                                     console=str(values.get("console") or ""), force=bool(values.get("force")))
        return {**res, "package": ctx.get_or_404(pkg_id)}

    # --- versions --------------------------------------------------------
    @app.get("/api/packages/{pkg_id}/releases", tags=["api"], dependencies=api)
    def api_releases(pkg_id: str):
        ctx.get_or_404(pkg_id)
        return db.list_releases(pkg_id)

    @app.post("/api/packages/{pkg_id}/releases/{rid}/restore", tags=["api"], dependencies=api)
    def api_restore(pkg_id: str, rid: int):
        pkg = db.restore_release(pkg_id, rid)
        if not pkg:
            raise HTTPException(404, "Version introuvable.")
        return pkg

    @app.delete("/api/packages/{pkg_id}/releases/{rid}", tags=["api"], status_code=204, dependencies=api)
    def api_delete_release(pkg_id: str, rid: int):
        if not db.get_release(pkg_id, rid):
            raise HTTPException(404, "Version introuvable.")
        db.delete_release(pkg_id, rid)

    # --- GitHub ----------------------------------------------------------
    @app.post("/api/packages/{pkg_id}/sync", tags=["api"], dependencies=api)
    async def api_sync(pkg_id: str, force: bool = False):
        pkg = ctx.get_or_404(pkg_id)
        try:
            msg = await run_in_threadpool(github.sync_package, db, pkg, settings.github_token, ctx.max_bytes, force)
        except github.SyncError as e:
            raise HTTPException(502, str(e))
        return {"message": msg, "package": ctx.get_or_404(pkg_id)}

    @app.post("/api/sync", tags=["api"], dependencies=api)
    async def api_sync_all():
        return await run_in_threadpool(github.sync_all, db, settings.github_token, ctx.max_bytes)

    # --- divers ----------------------------------------------------------
    @app.get("/api/validate", tags=["api"], dependencies=api)
    def api_validate():
        return {p["id"]: validate_package(p) for p in db.list_packages() if p["published"]}

    @app.get("/api/stats", tags=["api"], dependencies=api)
    def api_stats(days: int = 30):
        days = max(1, min(days, 365))
        return {
            "downloads": dict(db.series("download", days)),
            "index_fetches": dict(db.series("index", days)),
            "top": dict(db.top("download", days)),
            "all_time": {p["id"]: p["downloads"] for p in db.list_packages()},
        }

    @app.get("/api/backups", tags=["api"], dependencies=api)
    def api_backups():
        return backup.list_backups(settings)

    @app.post("/api/backups", tags=["api"], status_code=201, dependencies=api)
    async def api_backup_create():
        path = await run_in_threadpool(backup.create_backup, settings)
        return {"name": path.name, "size": path.stat().st_size}

    @app.get("/api/backups/{name}", tags=["api"], dependencies=api)
    def api_backup_download(name: str):
        path = backup.backup_path(settings, name)
        if not path:
            raise HTTPException(404, "Sauvegarde introuvable.")
        return FileResponse(path, filename=name, media_type="application/gzip")
