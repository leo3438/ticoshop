"""ticoshop : gestion d'un dépôt pour le Store de tico + vitrine publique.

Lancement : uvicorn app.main:create_app --factory
"""
import asyncio
import contextlib
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import routes_admin, routes_api, routes_public
from .config import Settings
from .context import APP_DIR, Context
from .tasks import scheduler


def create_app(settings: Settings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ctx = Context(settings or Settings())

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(scheduler(ctx)) if ctx.settings.scheduler else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="ticoshop", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=ctx.settings.secret_key, same_site="lax", max_age=7 * 86400)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    routes_public.register(app, ctx)
    routes_admin.register(app, ctx)
    routes_api.register(app, ctx)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 303:
            return RedirectResponse(exc.headers["Location"], 303)
        if request.url.path.startswith(("/api/", "/index.json", "/diag/")):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        public = not request.url.path.startswith("/admin")
        return ctx.render(request, "error.html", public=public, status_code=exc.status_code,
                          status=exc.status_code, detail=exc.detail)

    app.state.ctx = ctx
    app.state.settings = ctx.settings
    app.state.db = ctx.db
    return app
