"""Interface d'administration (HTML)."""
import json
import re
import time
import urllib.request
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.datastructures import UploadFile as StarletteUpload

from . import backup, github, storage
from .context import Context
from .index import validate_package
from .security import hash_password

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")


def register(app: FastAPI, ctx: Context) -> None:
    db = ctx.db
    settings = ctx.settings
    admin = [Depends(ctx.admin_page)]

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "?"

    # --- connexion -------------------------------------------------------
    @app.get("/admin/login", include_in_schema=False)
    def login_form(request: Request):
        return ctx.render(request, "admin/login.html", error="")

    @app.post("/admin/login", include_in_schema=False)
    def login(request: Request, password: str = Form(...), username: str = Form("admin"), csrf: str = Form("")):
        ctx.check_csrf(request, csrf)
        ip = client_ip(request)
        wait = ctx.limiter.blocked_for(ip)
        if wait:
            return ctx.render(request, "admin/login.html", status_code=429,
                              error=f"Trop de tentatives. Réessayez dans {wait // 60 + 1} min.")
        username = username.strip() or "admin"
        if ctx.authenticate(username, password):
            ctx.limiter.reset(ip)
            request.session.clear()
            request.session["admin"] = username
            return RedirectResponse("/admin", 303)
        ctx.limiter.fail(ip)
        return ctx.render(request, "admin/login.html", status_code=401, error="Identifiant ou mot de passe incorrect.")

    @app.post("/admin/logout", include_in_schema=False)
    def logout(request: Request, csrf: str = Form("")):
        ctx.check_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/", 303)

    # --- tableau de bord & dépôt -----------------------------------------
    @app.get("/admin", include_in_schema=False, dependencies=admin)
    def dashboard(request: Request):
        packages = db.list_packages()
        problems = {p["id"]: validate_package(p, settings.files_dir) for p in packages}
        return ctx.render(request, "admin/dashboard.html", packages=packages, problems=problems)

    @app.get("/admin/repo", include_in_schema=False, dependencies=admin)
    def repo_form(request: Request):
        return ctx.render(request, "admin/repo.html", saved=False)

    @app.post("/admin/repo", include_in_schema=False, dependencies=admin)
    async def repo_save(request: Request):
        form = await request.form()
        ctx.check_csrf(request, str(form.get("csrf", "")))
        repo = db.get_repo()
        values: dict[str, Any] = {k: str(form.get(k, "")).strip() for k in ("name", "description", "maintainer", "website")}
        values["publish_versions"] = bool(form.get("publish_versions"))
        for kind in ("icon", "cover"):
            upload = form.get(f"{kind}_file")
            if isinstance(upload, StarletteUpload) and upload.filename:
                storage.delete_image(settings.media_dir, repo[kind])
                values[kind] = await ctx.store_image(upload)  # type: ignore[arg-type]
            elif form.get(f"{kind}_remove"):
                storage.delete_image(settings.media_dir, repo[kind])
                values[kind] = ""
        db.set_repo(values)
        return ctx.render(request, "admin/repo.html", saved=True)

    # --- paquets ---------------------------------------------------------
    def package_page(request: Request, pkg: dict[str, Any] | None, error: str = "", status_code: int = 200) -> Response:
        return ctx.render(
            request, "admin/package.html", status_code=status_code, pkg=pkg, error=error,
            problems=validate_package(pkg, settings.files_dir) if pkg else [],
            releases=db.list_releases(pkg["id"]) if pkg else [],
        )

    @app.get("/admin/packages/new", include_in_schema=False, dependencies=admin)
    def new_form(request: Request):
        return package_page(request, None)

    @app.post("/admin/packages/new", include_in_schema=False, dependencies=admin)
    async def new_package(request: Request):
        form = await request.form()
        ctx.check_csrf(request, str(form.get("csrf", "")))
        pkg_id = str(form.get("id", "")).strip().lower()
        if not ID_RE.match(pkg_id):
            return package_page(request, None, "Identifiant invalide : lettres minuscules, chiffres, « . », « _ », « - ».", 400)
        if db.get_package(pkg_id):
            return package_page(request, None, "Cet identifiant existe déjà.", 409)
        db.create_package(pkg_id, {"name": str(form.get("name") or pkg_id), "published": False})
        return await save_form(request, pkg_id, form)

    @app.get("/admin/packages/{pkg_id}", include_in_schema=False, dependencies=admin)
    def edit_form(request: Request, pkg_id: str):
        return package_page(request, ctx.get_or_404(pkg_id))

    @app.post("/admin/packages/{pkg_id}", include_in_schema=False, dependencies=admin)
    async def edit_package(request: Request, pkg_id: str):
        ctx.get_or_404(pkg_id)
        form = await request.form()
        ctx.check_csrf(request, str(form.get("csrf", "")))
        return await save_form(request, pkg_id, form)

    async def save_form(request: Request, pkg_id: str, form: Any) -> Response:
        pkg = ctx.get_or_404(pkg_id)
        values: dict[str, Any] = {
            k: str(form.get(k, "")).strip()
            for k in ("name", "version", "author", "category", "type", "tagline", "source", "url",
                      "magnet", "torrent_file", "install", "sha256", "github_repo", "github_asset", "console")
        }
        values["description"] = str(form.get("description", ""))
        values["changelog"] = str(form.get("changelog", ""))
        values["tags"] = [t.strip() for t in str(form.get("tags", "")).split(",") if t.strip()]
        for flag in ("featured", "published", "keep_archive", "extract_to_folder", "auto_update"):
            values[flag] = bool(form.get(flag))
        if values["source"] != "upload":
            values["filename"] = str(form.get("filename", "")).strip()
            values["size"] = int(str(form.get("size") or 0) or 0)
        if values["github_repo"] and not github.REPO_RE.match(values["github_repo"]):
            return package_page(request, pkg, "Dépôt GitHub attendu sous la forme propriétaire/nom.", 400)
        try:
            extra = json.loads(str(form.get("extra") or "{}"))
            if not isinstance(extra, dict):
                raise ValueError
            values["extra"] = extra
        except ValueError:
            return package_page(request, pkg, "Le champ « Champs supplémentaires » doit être un objet JSON.", 400)

        for kind in ("icon", "banner"):
            upload = form.get(f"{kind}_file")
            if isinstance(upload, StarletteUpload) and upload.filename:
                storage.delete_image(settings.media_dir, pkg[kind])
                values[kind] = await ctx.store_image(upload)  # type: ignore[arg-type]
            elif form.get(f"{kind}_remove"):
                storage.delete_image(settings.media_dir, pkg[kind])
                values[kind] = ""
        shots = [s for s in pkg["screenshots"] if s not in form.getlist("screenshot_remove")]
        for s in set(pkg["screenshots"]) - set(shots):
            storage.delete_image(settings.media_dir, s)
        for upload in form.getlist("screenshot_files"):
            if isinstance(upload, StarletteUpload) and upload.filename:
                shots.append(await ctx.store_image(upload))  # type: ignore[arg-type]
        values["screenshots"] = shots

        upload = form.get("package_file")
        if values["source"] == "upload" and isinstance(upload, StarletteUpload) and upload.filename:
            # Fichier et champs du formulaire dans la même mise à jour (historique cohérent).
            values.pop("sha256", None)
            info = await ctx.store_upload(pkg_id, upload, values)  # type: ignore[arg-type]
            module, pack = info["module"], info["pack"]
            if info["rom_path"]:
                how = "reconnue (No-Intro)" if info.get("identified") else "nom nettoyé, ROM non reconnue par son CRC"
                request.session["flash"] = (f"ROM {how} et emballée dans un zip ({info['rom_path']}) : "
                                            "c'est le format que tico installe.")
            if pack:
                parts = [f"{len(pack['consoles'])} console(s)"] + [f"{n} {k}" for k, n in pack["counts"].items() if n]
                if pack["colors"]:
                    parts.append("couleurs")
                msg = f"Pack converti au format tico 0.8 : {', '.join(parts)}."
                if pack.get("soundpack_packages"):
                    msg += f" Soundpack livré avec : paquet « {', '.join(pack['soundpack_packages'])} » créé."
                request.session["flash"] = msg
            if module:
                consoles = ", ".join(s["title"] for s in module["systems"]) or "aucune console déclarée"
                request.session["flash"] = f"Module tico « {module['name']} » détecté ({consoles}) : fiche pré-remplie."
        else:
            db.update_package(pkg_id, values)
        if values["source"] == "url" and form.get("compute_checksum"):
            try:
                await ctx.fetch_checksum(ctx.get_or_404(pkg_id))
            except HTTPException as e:
                return package_page(request, ctx.get_or_404(pkg_id), str(e.detail), e.status_code)
        request.session.setdefault("flash", "Enregistré.")
        return RedirectResponse(f"/admin/packages/{pkg_id}", 303)

    @app.post("/admin/packages/{pkg_id}/delete", include_in_schema=False, dependencies=admin)
    def delete_package_form(request: Request, pkg_id: str, csrf: str = Form("")):
        ctx.check_csrf(request, csrf)
        ctx.remove_package(ctx.get_or_404(pkg_id))
        return RedirectResponse("/admin", 303)

    @app.post("/admin/packages/{pkg_id}/sync", include_in_schema=False, dependencies=admin)
    async def sync_package(request: Request, pkg_id: str, csrf: str = Form("")):
        ctx.check_csrf(request, csrf)
        pkg = ctx.get_or_404(pkg_id)
        try:
            msg = await run_in_threadpool(github.sync_package, db, pkg, settings.github_token, ctx.max_bytes, True)
            request.session["flash"] = f"GitHub : {msg}."
        except github.SyncError as e:
            request.session["flash"] = f"GitHub : échec, {e}."
        return RedirectResponse(f"/admin/packages/{pkg_id}", 303)

    @app.post("/admin/packages/{pkg_id}/covers", include_in_schema=False, dependencies=admin)
    async def find_covers(request: Request, pkg_id: str, csrf: str = Form(""), cover_name: str = Form(""),
                          console: str = Form(""), force: str = Form("")):
        ctx.check_csrf(request, csrf)
        try:
            res = await ctx.apply_covers(pkg_id, name=cover_name.strip(), console=console, force=bool(force))
        except HTTPException as e:
            request.session["flash"] = str(e.detail)
            return RedirectResponse(f"/admin/packages/{pkg_id}", 303)
        if res["found"] and res["added"]:
            request.session["flash"] = f"Images trouvées pour « {res['found']} » : {', '.join(res['added'])}."
        elif res["found"]:
            request.session["flash"] = f"Images trouvées pour « {res['found']} », mais le paquet en a déjà (cochez « Remplacer »)."
        else:
            request.session["flash"] = (f"Aucune jaquette trouvée pour « {res['tried']} ». Indiquez le nom exact "
                                        "No-Intro / Redump du jeu, ex. « Super Mario World (USA) ».")
        return RedirectResponse(f"/admin/packages/{pkg_id}", 303)

    @app.post("/admin/packages/{pkg_id}/releases/{rid}/restore", include_in_schema=False, dependencies=admin)
    def restore_release(request: Request, pkg_id: str, rid: int, csrf: str = Form("")):
        ctx.check_csrf(request, csrf)
        if not db.restore_release(pkg_id, rid):
            raise HTTPException(404, "Version introuvable.")
        request.session["flash"] = "Version republiée (l'ancienne version courante est dans l'historique)."
        return RedirectResponse(f"/admin/packages/{pkg_id}", 303)

    @app.post("/admin/packages/{pkg_id}/releases/{rid}/delete", include_in_schema=False, dependencies=admin)
    def delete_release(request: Request, pkg_id: str, rid: int, csrf: str = Form("")):
        ctx.check_csrf(request, csrf)
        db.delete_release(pkg_id, rid)
        request.session["flash"] = "Version supprimée."
        return RedirectResponse(f"/admin/packages/{pkg_id}", 303)

    # --- statistiques ----------------------------------------------------
    @app.get("/admin/stats", include_in_schema=False, dependencies=admin)
    def stats(request: Request, days: int = 30):
        days = max(7, min(days, 365))
        names = {p["id"]: p["name"] for p in db.list_packages()}
        return ctx.render(
            request, "admin/stats.html", days=days,
            downloads=db.series("download", days), fetches=db.series("index", days),
            top=[(names.get(k, k), k, n) for k, n in db.top("download", days)],
            all_time=sorted(db.list_packages(), key=lambda p: p["downloads"], reverse=True)[:10],
        )

    # --- sauvegardes -----------------------------------------------------
    @app.get("/admin/backups", include_in_schema=False, dependencies=admin)
    def backups(request: Request):
        return ctx.render(request, "admin/backups.html", backups=backup.list_backups(settings))

    @app.post("/admin/backups", include_in_schema=False, dependencies=admin)
    async def backup_now(request: Request, csrf: str = Form("")):
        ctx.check_csrf(request, csrf)
        path = await run_in_threadpool(backup.create_backup, settings)
        request.session["flash"] = f"Sauvegarde créée : {path.name}"
        return RedirectResponse("/admin/backups", 303)

    @app.get("/admin/backups/{name}", include_in_schema=False, dependencies=admin)
    def backup_download(name: str):
        path = backup.backup_path(settings, name)
        if not path:
            raise HTTPException(404)
        return FileResponse(path, filename=name, media_type="application/gzip")

    # --- comptes ---------------------------------------------------------
    @app.get("/admin/users", include_in_schema=False, dependencies=admin)
    def users(request: Request):
        return ctx.render(request, "admin/users.html", users=db.list_users(), error="")

    @app.post("/admin/users", include_in_schema=False, dependencies=admin)
    def user_save(request: Request, username: str = Form(...), password: str = Form(...), csrf: str = Form("")):
        ctx.check_csrf(request, csrf)
        username = username.strip()
        error = ""
        if not USER_RE.match(username):
            error = "Identifiant : 2 à 32 caractères (lettres, chiffres, . _ -)."
        elif len(password) < 10:
            error = "Mot de passe : 10 caractères minimum."
        if error:
            return ctx.render(request, "admin/users.html", status_code=400, users=db.list_users(), error=error)
        db.set_user(username, hash_password(password))
        request.session["flash"] = f"Compte « {username} » enregistré."
        return RedirectResponse("/admin/users", 303)

    @app.post("/admin/users/{username}/delete", include_in_schema=False, dependencies=admin)
    def user_delete(request: Request, username: str, csrf: str = Form("")):
        ctx.check_csrf(request, csrf)
        if username == request.session.get("admin"):
            request.session["flash"] = "Vous ne pouvez pas supprimer votre propre compte."
        else:
            db.delete_user(username)
            request.session["flash"] = f"Compte « {username} » supprimé."
        return RedirectResponse("/admin/users", 303)

    # --- diagnostic ------------------------------------------------------
    @app.get("/admin/diagnostic", include_in_schema=False, dependencies=admin)
    async def diagnostic_page(request: Request):
        base = ctx.base_url(request)
        checks: list[tuple[str, str, str]] = []  # (niveau ok|warn|err, titre, détail)

        def add(level: str, title: str, detail: str = "") -> None:
            checks.append((level, title, detail))

        if settings.public_url:
            add("ok", "Adresse publique définie", settings.public_url)
            ok, detail = await run_in_threadpool(probe, f"{settings.public_url}/healthz")
            add("ok" if ok else "err", "Le shop répond à son adresse publique" if ok else "Adresse publique injoignable depuis le serveur", detail)
            if not settings.public_url.startswith("https://"):
                add("warn", "Pas de HTTPS",
                    "Les téléchargements fonctionnent en http, mais tico attend des images (icônes, captures) en "
                    "https:// : elles peuvent ne pas s'afficher. Sur Internet, activez Caddy (install.sh --domain).")
        else:
            add("warn", "TICOSHOP_PUBLIC_URL non définie", f"Les liens de index.json utilisent l'adresse de la requête ({base}).")
        if settings.admin_password in ("", "change-moi"):
            add("err", "Mot de passe admin par défaut", "Changez TICOSHOP_ADMIN_PASSWORD dans .env.")
        add("ok" if settings.api_token else "warn", "Jeton d'API", "défini" if settings.api_token else "absent : le CLI ne pourra pas se connecter")

        packages = db.list_packages()
        broken = {p["id"]: validate_package(p, settings.files_dir) for p in packages if p["published"]}
        broken = {k: v for k, v in broken.items() if v}
        add("ok" if not broken else "warn", f"{len(packages)} paquet(s), {len(broken)} avec des problèmes",
            "; ".join(f"{k} : {' '.join(v)}" for k, v in broken.items()))
        synced = [p for p in packages if p["auto_update"]]
        errors = [p for p in synced if p["sync_error"]]
        if synced:
            add("ok" if not errors else "warn", f"Synchronisation GitHub : {len(synced)} paquet(s) suivis",
                "; ".join(f"{p['id']} : {p['sync_error']}" for p in errors)
                + ("" if settings.github_token else " (sans TICOSHOP_GITHUB_TOKEN : 60 requêtes/heure)"))
        add("ok" if settings.webhook_url else "warn", "Notifications",
            "webhook configuré" if settings.webhook_url else "TICOSHOP_WEBHOOK_URL non défini")
        backups = backup.list_backups(settings)
        if settings.backup_hours <= 0:
            add("warn", "Sauvegardes automatiques désactivées", "TICOSHOP_BACKUP_HOURS=0")
        elif not backups:
            add("warn", "Aucune sauvegarde pour l'instant", f"Prévue toutes les {settings.backup_hours} h.")
        else:
            age = (time.time() - backups[0]["time"]) / 3600
            add("ok" if age < settings.backup_hours * 2 else "warn", "Dernière sauvegarde", f"il y a {age:.0f} h ({backups[0]['name']})")
        return ctx.render(request, "admin/diagnostic.html", checks=checks, diag_url=f"{base}/diag/index.json")


def probe(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ticoshop"}), timeout=5) as r:
            return r.status == 200, f"HTTP {r.status}"
    except Exception as e:
        return False, str(e)
