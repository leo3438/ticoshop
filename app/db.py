"""Stockage SQLite : métadonnées du dépôt et paquets du shop."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
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
"""

JSON_FIELDS = {"tags": list, "screenshots": list, "extra": dict}
BOOL_FIELDS = {"featured", "published", "extract", "extract_to_folder"}

REPO_DEFAULTS = {
    "name": "Mon shop tico",
    "description": "",
    "maintainer": "",
    "website": "",
    "icon": "",
    "cover": "",
}

# Colonnes modifiables via l'admin ou l'API.
EDITABLE = [
    "name", "version", "author", "category", "type", "tags", "tagline",
    "description", "changelog", "icon", "banner", "screenshots", "featured",
    "published", "source", "url", "magnet", "torrent_file", "filename",
    "sha256", "size", "install", "extract", "extract_to_folder", "extra",
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

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
    def _encode(values: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in values.items():
            if k not in EDITABLE:
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
        data = self._encode(values)
        now = time.time()
        data.update(id=pkg_id, created_at=now, updated_at=now)
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self.conn() as c:
            c.execute(f"INSERT INTO packages ({cols}) VALUES ({marks})", list(data.values()))
        return self.get_package(pkg_id)  # type: ignore[return-value]

    def update_package(self, pkg_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        data = self._encode(values)
        if data:
            data["updated_at"] = time.time()
            sets = ", ".join(f"{k} = ?" for k in data)
            with self.conn() as c:
                c.execute(f"UPDATE packages SET {sets} WHERE id = ?", [*data.values(), pkg_id])
        return self.get_package(pkg_id)

    def set_local_file(self, pkg_id: str, local_file: str, filename: str, sha256: str, size: int) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE packages SET local_file = ?, filename = ?, sha256 = ?, size = ?, "
                "source = 'upload', updated_at = ? WHERE id = ?",
                (local_file, filename, sha256, size, time.time(), pkg_id),
            )

    def delete_package(self, pkg_id: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM packages WHERE id = ?", (pkg_id,))

    def count_download(self, pkg_id: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE packages SET downloads = downloads + 1 WHERE id = ?", (pkg_id,))
