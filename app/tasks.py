"""Tâches de fond : synchronisation GitHub et sauvegardes automatiques."""
import asyncio
import logging
import time

from fastapi.concurrency import run_in_threadpool

from . import backup, github
from .context import Context

log = logging.getLogger("ticoshop.tasks")
TICK = 60  # secondes


async def scheduler(ctx: Context) -> None:
    settings = ctx.settings
    # Première synchro une minute après le démarrage, puis à intervalle fixe.
    next_sync = time.time() + TICK
    while True:
        await asyncio.sleep(TICK)
        try:
            if backup.backup_due(settings):
                path = await run_in_threadpool(backup.create_backup, settings)
                log.info("sauvegarde créée : %s", path.name)
            if settings.github_sync_hours > 0 and time.time() >= next_sync:
                next_sync = time.time() + settings.github_sync_hours * 3600
                results = await run_in_threadpool(github.sync_all, ctx.db, settings.github_token, ctx.max_bytes)
                for pkg_id, msg in results.items():
                    log.info("github %s : %s", pkg_id, msg)
        except Exception:
            log.exception("tâche de fond en échec")
