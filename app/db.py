"""Stockage SQLite : dépôt, paquets, versions, comptes admin et statistiques."""
import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

# Chaque entrée est appliquée une fois, dans l'ordre (PRAGMA user_version).
MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS repo (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS packages (
        id                TEXT PRIMARY KEY,
        name              TEXT NOT NULL,
        version           TEXT NOT NULL DEFAULT '',
        author            TEXT NOT NULL DEFAULT '',
        category          TEXT NOT NULL DEFAULT 'app',
        type              TEXT NOT NULL DEFAULT 'zip',
        tags              TEXT NOT NULL DEFAULT '[]',
        tagline           TEXT NOT NULL DEFAULT '',
        description       TEXT NOT NULL DEFAULT '',
        changelog         TEXT NOT NULL DEFAULT '',
        icon              TEXT NOT NULL DEFAULT '',
        banner            TEXT NOT NULL DEFAULT '',
        screenshots       TEXT NOT NULL DEFAULT '[]',
        featured          INTEGER NOT NULL DEFAULT 0,
        published         INTEGER NOT NULL DEFAULT 1,
        source            TEXT NOT NULL DEFAULT 'upload',
        url               TEXT NOT NULL DEFAULT '',
        magnet            TEXT NOT NULL DEFAULT '',
        torrent_file      TEXT NOT NULL DEFAULT '',
        local_file        TEXT NOT NULL DEFAULT '',
        filename          TEXT NOT NULL DEFAULT '',
        sha256            TEXT NOT NULL DEFAULT '',
        size              INTEGER NOT NULL DEFAULT 0,
        install           TEXT NOT NULL DEFAULT '',
        extract           INTEGER NOT NULL DEFAULT 0,
        extract_to_folder INTEGER NOT NULL DEFAULT 0,
        extra             TEXT NOT NULL DEFAULT '{}',
        downloads         INTEGER NOT NULL DEFAULT 0,
        created_at        REAL NOT NULL,
        updated_at        REAL NOT NULL
    );
    """,
    """
    ALTER TABLE packages ADD COLUMN github_repo  TEXT NOT NULL DEFAULT '';
    ALTER TABLE packages ADD COLUMN github_asset TEXT NOT NULL DEFAULT '';
    ALTER TABLE packages ADD COLUMN auto_update  INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE packages ADD COLUMN last_sync    REAL NOT NULL DEFAULT 0;
    ALTER TABLE packages ADD COLUMN sync_error   TEXT NOT NULL DEFAULT '';
    CREATE TABLE releases (
        rid          INTEGER PRIMARY KEY AUTOINCREMENT,
        package_id   TEXT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
        version      TEXT NOT NULL DEFAULT '',
        changelog    TEXT NOT NULL DEFAULT '',
        source       TEXT NOT NULL,
        url          TEXT NOT NULL DEFAULT '',
        magnet       TEXT NOT NULL DEFAULT '',
        torrent_file TEXT NOT NULL DEFAULT '',
        local_file   TEXT NOT NULL DEFAULT '',
        filename     TEXT NOT NULL DEFAULT '',
        sha256       TEXT NOT NULL DEFAULT '',
        size         INTEGER NOT NULL DEFAULT 0,
        created_at   REAL NOT NULL
    );
    CREATE INDEX releases_pkg ON releases(package_id, rid);
    CREATE TABLE users (
        username      TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        created_at    REAL NOT NULL
    );
    CREATE TABLE stats (
        day   TEXT NOT NULL,
        kind  TEXT NOT NULL,
        key   TEXT NOT NULL DEFAULT '',
        count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, kind, key)
    );
    """,
    """
    ALTER TABLE packages ADD COLUMN console TEXT NOT NULL DEFAULT '';
    """,
    # tico extrait les zips par défaut : « extract » ne sert plus, keep_archive = "extract": false.
    """
    ALTER TABLE packages ADD COLUMN keep_archive INTEGER NOT NULL DEFAULT 0;
    """,
]

JSON_FIELDS = {"tags": list, "screenshots": list, "extra": dict}
BOOL_FIELDS = {"featured", "published", "extract", "extract_to_folder", "auto_update", "keep_archive"}

REPO_DEFAULTS = {
    "name": "Mon shop tico",
    "description": "",
    "maintainer": "",
    "website": "",
    "icon": "",
    "cover": "",
    # "1" : les anciennes versions sont aussi listées dans index.json.
    "publish_versions": "",
}

# Colonnes modifiables via l'admin ou l'API.
EDITABLE = [
    "name", "version", "author", "category", "type", "tags", "tagline",
    "description", "changelog", "icon", "banner", "screenshots", "featured",
    "published", "source", "url", "magnet", "torrent_file", "filename",
    "sha256", "size", "install", "extract", "extract_to_folder", "extra",
    "github_repo", "github_asset", "auto_update", "console", "keep_archive",
]
# Champs système, modifiables seulement par le code.
INTERNAL = ["local_file", "last_sync", "sync_error"]

# Ce qui définit « une version » d'un paquet.
RELEASE_FIELDS = ["version", "changelog", "source", "url", "magnet", "torrent_file",
                  "local_file", "filename", "sha256", "size"]
# Une version est archivée quand le fichier publié change (corriger le numéro
# de version seul ne crée pas d'entrée). Pour étiqueter correctement l'ancienne
# version, envoyez le nouveau fichier et son numéro dans la même opération.
RELEASE_KEY = ["source", "url", "magnet", "torrent_file", "local_file", "sha256"]

Listener = Callable[[dict[str, Any] | None, dict[str, Any]], None]


def has_download(pkg: dict[str, Any]) -> bool:
    return bool(pkg.get("local_file") or pkg.get("url") or pkg.get("magnet"))


class Database:
    def __init__(self, path: Path, files_dir: Path | None = None, keep_versions: int = 10) -> None:
        self.path = path
        self.files_dir = files_dir
        self.keep_versions = keep_versions
        self.listeners: list[Listener] = []
        self.migrate()

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def migrate(self) -> None:
        with self.conn() as c:
            current = c.execute("PRAGMA user_version").fetchone()[0]
            for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
                c.executescript(script)
                c.execute(f"PRAGMA user_version = {version}")

    # --- dépôt -----------------------------------------------------------
    def get_repo(self) -> dict[str, str]:
        with self.conn() as c:
            rows = c.execute("SELECT key, value FROM repo").fetchall()
        repo = dict(REPO_DEFAULTS)
        repo.update({r["key"]: r["value"] for r in rows})
        return repo

    def set_repo(self, values: dict[str, Any]) -> None:
        with self.conn() as c:
            for k, v in values.items():
                if k in REPO_DEFAULTS:
                    if isinstance(v, bool):
                        v = "1" if v else ""
                    c.execute(
                        "INSERT INTO repo(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (k, str(v or "")),
                    )

    # --- paquets ---------------------------------------------------------
    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        pkg = dict(row)
        for f in JSON_FIELDS:
            try:
                pkg[f] = json.loads(pkg[f] or "null") or JSON_FIELDS[f]()
            except json.JSONDecodeError:
                pkg[f] = JSON_FIELDS[f]()
        for f in BOOL_FIELDS:
            pkg[f] = bool(pkg[f])
        return pkg

    @staticmethod
    def _encode(values: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
        out = {}
        for k, v in values.items():
            if k not in allowed:
                continue
            if k in JSON_FIELDS:
                v = json.dumps(v if v is not None else JSON_FIELDS[k]())
            elif k in BOOL_FIELDS:
                v = 1 if v else 0
            elif k == "size":
                v = int(v or 0)
            elif v is None:
                v = ""
            out[k] = v
        return out

    def list_packages(self, published_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM packages"
        if published_only:
            sql += " WHERE published = 1"
        sql += " ORDER BY featured DESC, name COLLATE NOCASE"
        with self.conn() as c:
            return [self._decode(r) for r in c.execute(sql).fetchall()]

    def get_package(self, pkg_id: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM packages WHERE id = ?", (pkg_id,)).fetchone()
        return self._decode(row) if row else None

    def create_package(self, pkg_id: str, values: dict[str, Any]) -> dict[str, Any]:
        data = self._encode(values, EDITABLE)
        now = time.time()
        data.update(id=pkg_id, created_at=now, updated_at=now)
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self.conn() as c:
            c.execute(f"INSERT INTO packages ({cols}) VALUES ({marks})", list(data.values()))
        new = self.get_package(pkg_id)
        self._emit(None, new)  # type: ignore[arg-type]
        return new  # type: ignore[return-value]

    def update_package(self, pkg_id: str, values: dict[str, Any], internal: bool = False) -> dict[str, Any] | None:
        old = self.get_package(pkg_id)
        if old is None:
            return None
        data = self._encode(values, EDITABLE + INTERNAL if internal else EDITABLE)
        if not data:
            return old
        if any(k not in ("last_sync", "sync_error") for k in data):
            data["updated_at"] = time.time()
        sets = ", ".join(f"{k} = ?" for k in data)
        with self.conn() as c:
            c.execute(f"UPDATE packages SET {sets} WHERE id = ?", [*data.values(), pkg_id])
        new = self.get_package(pkg_id)
        self._archive(old, new)  # type: ignore[arg-type]
        self._emit(old, new)  # type: ignore[arg-type]
        return new

    def set_local_file(self, pkg_id: str, local_file: str, filename: str, sha256: str, size: int,
                       values: dict[str, Any] | None = None) -> None:
        """Publie un fichier envoyé ; « values » (version, changelog…) est appliqué en même temps."""
        self.update_package(pkg_id, {**(values or {}), "source": "upload", "local_file": local_file,
                                     "filename": filename, "sha256": sha256, "size": size}, internal=True)

    def delete_package(self, pkg_id: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM packages WHERE id = ?", (pkg_id,))

    def _emit(self, old: dict[str, Any] | None, new: dict[str, Any]) -> None:
        for listener in self.listeners:
            try:
                listener(old, new)
            except Exception:  # une notification ratée ne doit pas casser l'enregistrement
                pass

    # --- versions --------------------------------------------------------
    def _archive(self, old: dict[str, Any], new: dict[str, Any]) -> None:
        """Garde l'ancienne version dans l'historique quand le fichier publié change."""
        if not has_download(old) or [old[k] for k in RELEASE_KEY] == [new[k] for k in RELEASE_KEY]:
            return
        with self.conn() as c:
            dup = c.execute(
                "SELECT 1 FROM releases WHERE package_id = ? AND version = ? AND sha256 = ? "
                "AND local_file = ? AND url = ? AND magnet = ?",
                (old["id"], old["version"], old["sha256"], old["local_file"], old["url"], old["magnet"]),
            ).fetchone()
            if not dup:
                cols = ["package_id", *RELEASE_FIELDS, "created_at"]
                vals = [old["id"], *[old[k] for k in RELEASE_FIELDS], old["updated_at"]]
                c.execute(f"INSERT INTO releases ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        self._prune(old["id"])

    def _prune(self, pkg_id: str) -> None:
        with self.conn() as c:
            extra = c.execute(
                "SELECT rid FROM releases WHERE package_id = ? ORDER BY rid DESC LIMIT -1 OFFSET ?",
                (pkg_id, max(self.keep_versions, 0)),
            ).fetchall()
        for row in extra:
            self.delete_release(pkg_id, row["rid"])

    def list_releases(self, pkg_id: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM releases WHERE package_id = ? ORDER BY rid DESC", (pkg_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_release(self, pkg_id: str, rid: int) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM releases WHERE package_id = ? AND rid = ?", (pkg_id, rid)).fetchone()
        return dict(row) if row else None

    def restore_release(self, pkg_id: str, rid: int) -> dict[str, Any] | None:
        """Republie une ancienne version (la version actuelle passe dans l'historique)."""
        rel = self.get_release(pkg_id, rid)
        if not rel:
            return None
        with self.conn() as c:
            c.execute("DELETE FROM releases WHERE rid = ?", (rid,))
        return self.update_package(pkg_id, {k: rel[k] for k in RELEASE_FIELDS}, internal=True)

    def delete_release(self, pkg_id: str, rid: int) -> None:
        rel = self.get_release(pkg_id, rid)
        if not rel:
            return
        with self.conn() as c:
            c.execute("DELETE FROM releases WHERE rid = ?", (rid,))
        self._delete_file_if_unused(pkg_id, rel["local_file"])

    def _delete_file_if_unused(self, pkg_id: str, local_file: str) -> None:
        if not local_file or not self.files_dir:
            return
        with self.conn() as c:
            used = c.execute(
                "SELECT 1 FROM packages WHERE local_file = ? UNION SELECT 1 FROM releases WHERE local_file = ?",
                (local_file, local_file),
            ).fetchone()
        if used:
            return
        path = self.files_dir / local_file
        if path.resolve().is_relative_to(self.files_dir.resolve()) and path.is_file():
            path.unlink()
            # Dossier de version vide (files/<id>/<jeton>/) : on le retire aussi.
            if path.parent != self.files_dir / pkg_id and not any(path.parent.iterdir()):
                path.parent.rmdir()

    # --- statistiques ----------------------------------------------------
    def bump(self, kind: str, key: str = "") -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO stats(day, kind, key, count) VALUES(?, ?, ?, 1) "
                "ON CONFLICT(day, kind, key) DO UPDATE SET count = count + 1",
                (date.today().isoformat(), kind, key),
            )
            if kind == "download":
                c.execute("UPDATE packages SET downloads = downloads + 1 WHERE id = ?", (key,))

    def count_download(self, pkg_id: str) -> None:
        self.bump("download", pkg_id)

    def series(self, kind: str, days: int = 30, key: str | None = None) -> list[tuple[str, int]]:
        start = date.today() - timedelta(days=days - 1)
        sql = "SELECT day, SUM(count) AS n FROM stats WHERE kind = ? AND day >= ?"
        args: list[Any] = [kind, start.isoformat()]
        if key is not None:
            sql += " AND key = ?"
            args.append(key)
        with self.conn() as c:
            counts = {r["day"]: r["n"] for r in c.execute(sql + " GROUP BY day", args)}
        return [((start + timedelta(days=i)).isoformat(), counts.get((start + timedelta(days=i)).isoformat(), 0))
                for i in range(days)]

    def top(self, kind: str, days: int = 30, limit: int = 10) -> list[tuple[str, int]]:
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        with self.conn() as c:
            rows = c.execute(
                "SELECT key, SUM(count) AS n FROM stats WHERE kind = ? AND day >= ? "
                "GROUP BY key ORDER BY n DESC LIMIT ?",
                (kind, start, limit),
            ).fetchall()
        return [(r["key"], r["n"]) for r in rows]

    # --- comptes admin ---------------------------------------------------
    def list_users(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT username, created_at FROM users ORDER BY username")]

    def get_password_hash(self, username: str) -> str | None:
        with self.conn() as c:
            row = c.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
        return row["password_hash"] if row else None

    def set_user(self, username: str, password_hash: str) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO users(username, password_hash, created_at) VALUES(?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash",
                (username, password_hash, time.time()),
            )

    def delete_user(self, username: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM users WHERE username = ?", (username,))
