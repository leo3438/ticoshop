"""Pages et fichiers publics : vitrine, index.json, téléchargements, RSS, diagnostic."""
import io
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from . import diagnostic
from .context import APP_DIR, Context
from .index import package_download_url, release_download_url


def qr_svg(text: str) -> Response:
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")


def register(app: FastAPI, ctx: Context) -> None:
    db = ctx.db

    @app.get("/", include_in_schema=False)
    def storefront(request: Request, q: str = "", category: str = ""):
        published = db.list_packages(published_only=True)
        packages = published
        if category:
            packages = [p for p in packages if p["category"] == category]
        if q:
            ql = q.lower()
            packages = [p for p in packages if ql in f"{p['name']} {p['tagline']} {' '.join(p['tags'])}".lower()]
        used = sorted({p["category"] for p in published})
        return ctx.render(request, "store.html", public=True, packages=packages, q=q, category=category,
                          used_categories=used)

    @app.get("/p/{pkg_id}", include_in_schema=False)
    def package_page(request: Request, pkg_id: str):
        pkg = ctx.get_or_404(pkg_id)
        if not pkg["published"] and not request.session.get("admin"):
            raise HTTPException(404, "Paquet introuvable.")
        base = ctx.base_url(request)
        releases = [
            {**r, "download": release_download_url(base, pkg_id, r)} for r in db.list_releases(pkg_id)
        ]
        return ctx.render(request, "package.html", public=True, pkg=pkg,
                          download=package_download_url(base, pkg), releases=releases)

    @app.get("/add", include_in_schema=False)
    def add_page(request: Request):
        return ctx.render(request, "add.html", public=True)

    @app.get("/index.json", tags=["tico"])
    @app.get("/t", include_in_schema=False)  # alias court, plus rapide à taper sur la Switch
    def index_json(request: Request):
        if not request.session.get("admin"):
            db.bump("index")
        return JSONResponse(ctx.current_index(request), headers={"Cache-Control": "no-cache"})

    @app.get("/qr.svg", include_in_schema=False)
    def qr(request: Request, target: str = "index"):
        url = f"{ctx.base_url(request)}/diag/index.json" if target == "diag" else ctx.index_url(request)
        return qr_svg(url)

    @app.get("/add-to-switch.sh", include_in_schema=False)
    def add_script(request: Request):
        script = (APP_DIR.parent / "scripts" / "add-to-switch.sh").read_text()
        script = script.replace("__TICOSHOP_INDEX_URL__", ctx.index_url(request))
        return PlainTextResponse(script, media_type="text/x-shellscript")

    @app.get("/feed.xml", include_in_schema=False)
    def feed(request: Request):
        base = ctx.base_url(request)
        repo = db.get_repo()
        packages = sorted(db.list_packages(published_only=True), key=lambda p: p["updated_at"], reverse=True)[:50]
        items = []
        for p in packages:
            version = f" v{p['version']}" if p["version"] else ""
            when = format_datetime(datetime.fromtimestamp(p["updated_at"], timezone.utc))
            body = p["changelog"] or p["tagline"] or p["description"]
            items.append(
                f"<item><title>{escape(p['name'] + version)}</title>"
                f"<link>{escape(base)}/p/{escape(p['id'])}</link>"
                f"<guid isPermaLink=\"false\">{escape(p['id'])}@{escape(p['version'] or str(int(p['updated_at'])))}</guid>"
                f"<pubDate>{when}</pubDate><category>{escape(p['category'])}</category>"
                f"<description>{escape(body)}</description></item>"
            )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            f"<title>{escape(repo['name'])}</title><link>{escape(base)}/</link>"
            f"<description>{escape(repo['description'] or repo['name'])}</description>"
            + "".join(items) + "</channel></rss>"
        )
        return Response(xml, media_type="application/rss+xml")

    def send_file(pkg_id: str, local_file: str, filename: str) -> FileResponse:
        path = ctx.settings.files_dir / local_file
        if not local_file or not path.is_file():
            raise HTTPException(404)
        db.count_download(pkg_id)
        return FileResponse(path, filename=filename, media_type="application/octet-stream")

    @app.get("/files/{pkg_id}/{filename}", include_in_schema=False)
    def download(pkg_id: str, filename: str):
        pkg = ctx.get_or_404(pkg_id)
        if not pkg["published"] or pkg["source"] != "upload" or pkg["filename"] != filename:
            raise HTTPException(404)
        return send_file(pkg_id, pkg["local_file"], filename)

    @app.get("/files/{pkg_id}/r/{rid}/{filename}", include_in_schema=False)
    def download_release(pkg_id: str, rid: int, filename: str):
        pkg = ctx.get_or_404(pkg_id)
        rel = db.get_release(pkg_id, rid)
        if not pkg["published"] or not rel or rel["source"] != "upload" or rel["filename"] != filename:
            raise HTTPException(404)
        return send_file(pkg_id, rel["local_file"], filename)

    @app.get("/media/{name}", include_in_schema=False)
    def media(name: str):
        path = ctx.settings.media_dir / Path(name).name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})

    # Dépôt de test (voir app/diagnostic.py) : public, la Switch doit pouvoir le lire.
    @app.get("/diag/index.json", include_in_schema=False)
    def diag_index(request: Request):
        return JSONResponse(diagnostic.test_index(ctx.base_url(request)), headers={"Cache-Control": "no-cache"})

    @app.get(f"/diag/{diagnostic.PKG_ID}.zip", include_in_schema=False)
    def diag_zip():
        return Response(diagnostic.test_zip(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{diagnostic.PKG_ID}.zip"'})

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}
