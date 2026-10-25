import hashlib
from pathlib import Path
import io
import json
import re
import tarfile
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import github, notify
from app.config import Settings
from app.main import create_app

TOKEN = "test-token"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_app(tmp_path, monkeypatch, **env):
    defaults = {
        "TICOSHOP_DATA_DIR": str(tmp_path),
        "TICOSHOP_PUBLIC_URL": "https://shop.test",
        "TICOSHOP_ADMIN_PASSWORD": "secret",
        "TICOSHOP_API_TOKEN": TOKEN,
        "TICOSHOP_SCHEDULER": "0",
        "TICOSHOP_WEBHOOK_URL": "",
        "TICOSHOP_DATS_URL": "http://127.0.0.1:9/dats",  # pas de réseau dans les tests
    }
    defaults.update(env)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)
    return create_app(Settings())


@pytest.fixture
def client(tmp_path, monkeypatch):
    return TestClient(make_app(tmp_path, monkeypatch))


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def csrf_of(client, url="/admin/login"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(url).text).group(1)


def admin_login(client, username="admin", password="secret"):
    r = client.post("/admin/login", data={"username": username, "password": password, "csrf": csrf_of(client)},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    return csrf_of(client, "/admin")  # la session est régénérée à la connexion


def module_zip(module_id="picodrive", name="PicoDrive"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{module_id}/module.json", json.dumps({
            "id": module_id, "name": name,
            "systems": [{"slug": "sega-32x", "title": "Sega 32X", "short_title": "32X"}],
        }))
        zf.writestr(f"{module_id}/tico-{module_id}.nro", b"NRO0")
    return buf.getvalue()


# --- index.json -------------------------------------------------------------
def test_api_requires_token(client):
    assert client.get("/api/packages").status_code == 401
    assert client.get("/api/packages", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_index_schema_and_upload(client):
    r = client.post("/api/packages", headers=auth(), json={
        "id": "hello", "name": "Hello", "version": "1.2", "category": "homebrew", "type": "nro",
        "tags": ["demo"], "install": "/switch/hello/", "featured": True,
    })
    assert r.status_code == 201, r.text
    payload = b"NRO0" * 1000
    r = client.post("/api/packages/hello/file", headers=auth(), files={"file": ("hello world.nro", payload)})
    assert r.status_code == 200
    assert r.json()["sha256"] == hashlib.sha256(payload).hexdigest()
    client.post("/api/packages/hello/media", headers=auth(), data={"kind": "icon"}, files={"file": ("i.png", PNG)})

    index = client.get("/index.json").json()
    assert index["schema"] == 1
    pkg = index["packages"][0]
    assert pkg["id"] == "hello"
    assert pkg["url"] == "https://shop.test/files/hello/hello%20world.nro"
    assert pkg["sha256"] == hashlib.sha256(payload).hexdigest()
    assert pkg["size"] == len(payload)
    assert pkg["install"] == "switch/hello"
    assert pkg["featured"] is True
    assert pkg["icon"].startswith("https://shop.test/media/")
    assert client.get("/t").json() == index

    dl = client.get("/files/hello/hello%20world.nro")
    assert dl.content == payload
    assert client.get("/api/packages/hello", headers=auth()).json()["downloads"] == 1


def test_unpublished_and_fileless_hidden(client):
    client.post("/api/packages", headers=auth(), json={"id": "draft", "published": False})
    client.post("/api/packages/draft/file", headers=auth(), files={"file": ("a.zip", b"zip")})
    client.post("/api/packages", headers=auth(), json={"id": "no-file"})
    assert client.get("/index.json").json()["packages"] == []
    assert client.get("/p/draft").status_code == 404


def test_validation(client):
    client.post("/api/packages", headers=auth(), json={
        "id": "bad", "source": "url", "url": "ftp://x", "install": "atmosphere/contents",
    })
    problems = client.get("/api/validate", headers=auth()).json()["bad"]
    assert any("http" in p for p in problems)
    assert any("dossier système" in p for p in problems)
    assert any("sha256" in p for p in problems)


def test_magnet_and_extra(client):
    client.post("/api/packages", headers=auth(), json={
        "id": "big-game", "source": "magnet", "magnet": "magnet:?xt=urn:btih:abc",
        "torrent_file": "game.zip", "type": "rom", "category": "game", "extra": {"console": "gba"},
    })
    pkg = client.get("/index.json").json()["packages"][0]
    assert pkg["magnet"].startswith("magnet:?")
    assert pkg["file"] == "game.zip"
    assert "url" not in pkg
    assert pkg["console"] == "gba"


def test_bad_ids_and_enums(client):
    assert client.post("/api/packages", headers=auth(), json={"id": "Bad ID"}).status_code == 400
    assert client.post("/api/packages", headers=auth(), json={"id": "ok", "type": "exe"}).status_code == 400
    assert client.post("/api/packages", headers=auth(), json={"id": "gh", "github_repo": "nope"}).status_code == 400
    client.post("/api/packages", headers=auth(), json={"id": "ok"})
    assert client.post("/api/packages", headers=auth(), json={"id": "ok"}).status_code == 409


def test_delete_removes_files(client, tmp_path):
    client.post("/api/packages", headers=auth(), json={"id": "gone"})
    client.post("/api/packages/gone/file", headers=auth(), files={"file": ("a.zip", b"zip")})
    assert list((tmp_path / "files" / "gone").glob("*/a.zip"))
    assert client.delete("/api/packages/gone", headers=auth()).status_code == 204
    assert not (tmp_path / "files" / "gone").exists()


def test_repo_metadata(client):
    client.put("/api/repo", headers=auth(), json={"name": "Shop Opens", "maintainer": "Opens"})
    index = client.get("/index.json").json()
    assert index["name"] == "Shop Opens" and index["maintainer"] == "Opens"


# --- versions ---------------------------------------------------------------
def test_versions_history_restore_and_publish(client, tmp_path):
    client.post("/api/packages", headers=auth(), json={"id": "app", "version": "0.9"})
    client.patch("/api/packages/app", headers=auth(), json={"version": "1.0"})  # correction : pas archivée
    client.post("/api/packages/app/file", headers=auth(), files={"file": ("app.nro", b"v1")})
    client.post("/api/packages/app/file", headers=auth(), data={"version": "2.0", "changelog": "Nouveautés"},
                files={"file": ("app.nro", b"v2")})
    client.post("/api/packages/app/file", headers=auth(), data={"version": "3.0"},
                files={"file": ("app.nro", b"v3")})

    releases = client.get("/api/packages/app/releases", headers=auth()).json()
    assert [r["version"] for r in releases] == ["2.0", "1.0"]
    assert releases[0]["changelog"] == "Nouveautés"
    v1 = releases[-1]
    assert v1["sha256"] == hashlib.sha256(b"v1").hexdigest()
    # l'ancienne version reste téléchargeable
    assert client.get(f"/files/app/r/{v1['rid']}/app.nro").content == b"v1"
    assert client.get("/files/app/app.nro").content == b"v3"

    # publication des anciennes versions dans l'index (même id)
    assert len(client.get("/index.json").json()["packages"]) == 1
    client.put("/api/repo", headers=auth(), json={"publish_versions": True})
    entries = client.get("/index.json").json()["packages"]
    assert [e["id"] for e in entries] == ["app"] * 3
    assert [e["version"] for e in entries] == ["3.0", "2.0", "1.0"]
    assert entries[-1]["url"].endswith(f"/files/app/r/{v1['rid']}/app.nro")

    # retour arrière
    pkg = client.post(f"/api/packages/app/releases/{v1['rid']}/restore", headers=auth()).json()
    assert pkg["version"] == "1.0" and pkg["sha256"] == hashlib.sha256(b"v1").hexdigest()
    assert client.get("/files/app/app.nro").content == b"v1"
    versions = [r["version"] for r in client.get("/api/packages/app/releases", headers=auth()).json()]
    assert versions == ["3.0", "2.0"]

    # suppression d'une version : son fichier disparaît s'il n'est plus utilisé
    for r in client.get("/api/packages/app/releases", headers=auth()).json():
        client.delete(f"/api/packages/app/releases/{r['rid']}", headers=auth())
    assert client.get("/api/packages/app/releases", headers=auth()).json() == []
    assert len(list((tmp_path / "files" / "app").glob("*/app.nro"))) == 1


def test_keep_versions_limit(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch, TICOSHOP_KEEP_VERSIONS="2"))
    client.post("/api/packages", headers=auth(), json={"id": "app"})
    for i in range(5):
        client.post("/api/packages/app/file", headers=auth(), data={"version": str(i)},
                    files={"file": ("app.nro", str(i).encode())})
    assert len(client.get("/api/packages/app/releases", headers=auth()).json()) == 2
    assert len(list((tmp_path / "files" / "app").glob("*/app.nro"))) == 3


# --- modules tico -----------------------------------------------------------
def test_module_zip_prefills_package(client):
    client.post("/api/packages", headers=auth(), json={"id": "picodrive"})
    r = client.post("/api/packages/picodrive/file", headers=auth(),
                    files={"file": ("tico-picodrive-module.zip", module_zip())}).json()
    assert r["module"]["name"] == "PicoDrive"
    assert r["type"] == "module" and r["category"] == "app"
    assert r["name"] == "PicoDrive" and r["tags"] == ["emulator", "sega-32x"]
    assert r["install"] == ""


def test_plain_zip_is_not_a_module(client):
    client.post("/api/packages", headers=auth(), json={"id": "port", "category": "port"})
    r = client.post("/api/packages/port/file", headers=auth(), files={"file": ("p.zip", b"not a zip")}).json()
    assert r["module"] is None and r["category"] == "port"


# --- GitHub -----------------------------------------------------------------
def test_github_sync(client, monkeypatch):
    calls = []

    def fake_get(url, token=""):
        calls.append(url)
        return {
            "tag_name": "v3.1.0", "body": "Notes",
            "assets": [
                {"name": "source.tar.gz", "browser_download_url": "https://gh/src", "size": 1},
                {"name": "tico-x-module.zip", "browser_download_url": "https://gh/x.zip", "size": 42,
                 "digest": "sha256:" + "a" * 64},
            ],
        }

    monkeypatch.setattr(github, "_get", fake_get)
    client.post("/api/packages", headers=auth(), json={
        "id": "x", "github_repo": "owner/tico-x", "github_asset": r"module\.zip$", "auto_update": True,
    })
    r = client.post("/api/packages/x/sync", headers=auth())
    assert r.status_code == 200, r.text
    pkg = r.json()["package"]
    assert pkg["version"] == "3.1.0" and pkg["url"] == "https://gh/x.zip"
    assert pkg["sha256"] == "a" * 64 and pkg["size"] == 42 and pkg["changelog"] == "Notes"
    assert calls == ["https://api.github.com/repos/owner/tico-x/releases/latest"]
    # deuxième passage : rien ne change
    assert "déjà à jour" in client.post("/api/sync", headers=auth()).json()["x"]


def test_github_sync_error_is_recorded(client, monkeypatch):
    def fail(url, token=""):
        raise github.SyncError("dépôt ou release introuvable")

    monkeypatch.setattr(github, "_get", fail)
    client.post("/api/packages", headers=auth(), json={"id": "y", "github_repo": "o/r", "auto_update": True})
    assert client.post("/api/packages/y/sync", headers=auth()).status_code == 502
    assert "introuvable" in client.get("/api/packages/y", headers=auth()).json()["sync_error"]


# --- notifications ----------------------------------------------------------
def test_webhook_events(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda url, body: sent.append(body))
    client = TestClient(make_app(tmp_path, monkeypatch,
                                 TICOSHOP_WEBHOOK_URL="https://discord.com/api/webhooks/1/abc"))
    client.post("/api/packages", headers=auth(), json={"id": "n", "name": "Nouveau", "version": "1"})
    assert sent == []  # pas encore de fichier
    client.post("/api/packages/n/file", headers=auth(), files={"file": ("n.nro", b"1")})
    client.patch("/api/packages/n", headers=auth(), json={"tagline": "juste du texte"})
    client.patch("/api/packages/n", headers=auth(), json={"version": "2"})
    titles = [b["embeds"][0]["title"] for b in sent]
    assert len(titles) == 2 and "Nouveau" in titles[0] and "Mise à jour" in titles[1]
    assert sent[1]["embeds"][0]["url"] == "https://shop.test/p/n"


def test_generic_webhook_payload():
    pkg = {"id": "a", "name": "A", "version": "1", "category": "app", "type": "nro",
           "tagline": "", "changelog": "", "description": "", "icon": ""}
    body = notify.payload("https://example.com/hook", "new", pkg, "https://s", "Shop")
    assert body["event"] == "new" and body["package"]["id"] == "a" and body["index"] == "https://s/index.json"


# --- statistiques, RSS, i18n, diagnostic --------------------------------------
def test_stats(client):
    client.post("/api/packages", headers=auth(), json={"id": "s"})
    client.post("/api/packages/s/file", headers=auth(), files={"file": ("s.nro", b"s")})
    for _ in range(3):
        client.get("/files/s/s.nro")
    client.get("/index.json")
    stats = client.get("/api/stats?days=7", headers=auth()).json()
    assert sum(stats["downloads"].values()) == 3 and stats["top"] == {"s": 3}
    assert sum(stats["index_fetches"].values()) == 1


def test_feed(client):
    client.post("/api/packages", headers=auth(), json={"id": "f", "name": "Flux & Co", "version": "1"})
    client.post("/api/packages/f/file", headers=auth(), files={"file": ("f.nro", b"f")})
    xml = client.get("/feed.xml")
    assert xml.headers["content-type"].startswith("application/rss+xml")
    assert "<title>Flux &amp; Co v1</title>" in xml.text and "https://shop.test/p/f" in xml.text


def test_public_i18n(client):
    assert "Ajouter à tico" in client.get("/add").text
    r = client.get("/add?lang=en")
    assert "Add to tico" in r.text and r.cookies.get("lang") == "en"
    assert "Add to tico" in client.get("/").text  # cookie mémorisé
    assert "Catalog" in TestClient(client.app).get("/", headers={"Accept-Language": "en-US,en;q=0.9"}).text


def test_diagnostic_repo(client):
    index = client.get("/diag/index.json").json()
    pkg = index["packages"][0]
    data = client.get("/diag/ticoshop-test.zip").content
    assert hashlib.sha256(data).hexdigest() == pkg["sha256"] and len(data) == pkg["size"]
    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == ["README.txt"]
    admin_login(client)
    page = client.get("/admin/diagnostic").text
    assert "https://shop.test/diag/index.json" in page


def test_public_pages_and_script(client):
    assert "index.json" in client.get("/").text
    assert "https://shop.test/index.json" in client.get("/add").text
    assert client.get("/qr.svg").headers["content-type"].startswith("image/svg")
    script = client.get("/add-to-switch.sh").text
    assert 'INDEX_URL="${2:-https://shop.test/index.json}"' in script


# --- admin ------------------------------------------------------------------
def test_admin_flow(client):
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    csrf = admin_login(client)
    r = client.post("/admin/packages/new", data={
        "csrf": csrf, "id": "web-pkg", "name": "Web", "version": "2.0", "category": "port", "type": "zip",
        "source": "upload", "tags": "a, b", "published": "on", "keep_archive": "on", "install": "tico/roms/gba", "extra": "{}",
    }, files={"package_file": ("web.zip", b"PK..."), "screenshot_files": ("s.png", PNG)}, follow_redirects=False)
    assert r.status_code == 303, r.text
    pkg = client.get("/api/packages/web-pkg").json()
    assert pkg["tags"] == ["a", "b"] and pkg["keep_archive"] and pkg["filename"] == "web.zip" and pkg["published"]
    assert len(pkg["screenshots"]) == 1
    for page in ("/admin", "/admin/packages/web-pkg", "/admin/stats", "/admin/backups", "/admin/users", "/admin/repo"):
        assert client.get(page).status_code == 200, page

    assert client.post("/admin/packages/web-pkg/delete", data={"csrf": "x"}).status_code == 400
    client.post("/admin/packages/web-pkg/delete", data={"csrf": csrf})
    assert client.get("/api/packages/web-pkg").status_code == 404


def test_admin_module_upload_flash(client):
    csrf = admin_login(client)
    r = client.post("/admin/packages/new", data={"csrf": csrf, "id": "pico", "source": "upload", "extra": "{}"},
                    files={"package_file": ("m.zip", module_zip())})
    assert "Module tico « PicoDrive » détecté" in r.text  # redirection suivie, message affiché une fois
    assert "détecté" not in client.get("/admin/packages/pico").text


def test_login_rate_limit(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch, TICOSHOP_LOGIN_MAX_ATTEMPTS="3"))
    for _ in range(3):
        r = client.post("/admin/login", data={"username": "admin", "password": "bad", "csrf": csrf_of(client)})
        assert r.status_code == 401
    r = client.post("/admin/login", data={"username": "admin", "password": "secret", "csrf": csrf_of(client)})
    assert r.status_code == 429


def test_users(client):
    csrf = admin_login(client)
    r = client.post("/admin/users", data={"csrf": csrf, "username": "alice", "password": "court"})
    assert r.status_code == 400
    client.post("/admin/users", data={"csrf": csrf, "username": "alice", "password": "un-long-mot-de-passe"})
    client.post("/admin/logout", data={"csrf": csrf})
    admin_login(client, "alice", "un-long-mot-de-passe")
    assert client.get("/admin").status_code == 200
    r = client.post("/admin/login", data={"username": "alice", "password": "faux", "csrf": csrf_of(client)})
    assert r.status_code == 401


def test_backups(client, tmp_path):
    client.post("/api/packages", headers=auth(), json={"id": "b"})
    client.post("/api/packages/b/file", headers=auth(), files={"file": ("b.nro", b"data")})
    r = client.post("/api/backups", headers=auth())
    assert r.status_code == 201
    name = r.json()["name"]
    archive = client.get(f"/api/backups/{name}", headers=auth()).content
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        names = tar.getnames()
    assert "data/ticoshop.db" in names and any(n.endswith("/b.nro") for n in names)
    assert not any("backups" in n for n in names)
    assert client.get("/api/backups/../../etc/passwd", headers=auth()).status_code == 404


def test_migration_from_v1_database(tmp_path, monkeypatch):
    import sqlite3

    from app.db import MIGRATIONS
    con = sqlite3.connect(tmp_path / "ticoshop.db")
    con.executescript(MIGRATIONS[0])
    con.execute("INSERT INTO packages(id, name, created_at, updated_at) VALUES('old', 'Old', 0, 0)")
    con.commit()
    con.close()
    client = TestClient(make_app(tmp_path, monkeypatch))
    pkg = client.get("/api/packages/old", headers=auth()).json()
    assert pkg["name"] == "Old" and pkg["auto_update"] is False


def test_scheduler_runs_backup_and_sync(tmp_path, monkeypatch):
    import asyncio

    from app import tasks

    app = make_app(tmp_path, monkeypatch, TICOSHOP_BACKUP_HOURS="1", TICOSHOP_GITHUB_SYNC_HOURS="1")
    ctx = app.state.ctx
    synced = []
    monkeypatch.setattr(tasks, "TICK", 0.01)
    monkeypatch.setattr(tasks.github, "sync_all", lambda db, token, max_bytes: synced.append(1) or {})

    async def run():
        task = asyncio.create_task(tasks.scheduler(ctx))
        await asyncio.sleep(0.5)
        task.cancel()

    asyncio.run(run())
    assert len(list((tmp_path / "backups").glob("ticoshop-*.tar.gz"))) == 1  # une seule : la suivante dans 1 h
    assert synced == [1]


# --- jaquettes & dossiers autorisés -------------------------------------------
def test_covers_auto_on_rom_upload(client, monkeypatch):
    from app import covers

    PNG_BOX, PNG_SNAP = PNG + b"box", PNG + b"snap"
    asked = []

    def fake_fetch(url, timeout=20, max_bytes=0):
        asked.append(url)
        if "Super%20Mario%20World%20%28USA%29.png" not in url:
            return None
        return PNG_BOX if "Named_Boxarts" in url else PNG_SNAP if "Named_Snaps" in url else None

    monkeypatch.setattr(covers, "fetch", fake_fetch)
    client.post("/api/packages", headers=auth(), json={"id": "smw", "category": "game", "type": "rom"})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Super Mario World (USA) [!].sfc", b"rom")
    client.post("/api/packages/smw/file", headers=auth(), files={"file": ("smw.zip", buf.getvalue())})
    pkg = client.get("/api/packages/smw", headers=auth()).json()
    assert pkg["console"] == "snes"
    assert pkg["icon"].startswith("/media/") and len(pkg["screenshots"]) == 1 and pkg["banner"] == ""
    assert client.get(pkg["icon"]).content == PNG_BOX
    assert asked[0].startswith("https://thumbnails.libretro.com/Nintendo%20-%20Super%20Nintendo%20Entertainment%20System/Named_Boxarts/")
    entry = client.get("/index.json").json()["packages"][0]
    assert entry["icon"].startswith("https://shop.test/media/") and "console" not in entry


def test_covers_manual_and_not_found(client, monkeypatch):
    from app import covers

    monkeypatch.setattr(covers, "fetch", lambda url, **kw: None)
    client.post("/api/packages", headers=auth(), json={"id": "g", "category": "game", "type": "rom"})
    assert client.post("/api/packages/g/covers", headers=auth(), json={}).status_code == 400  # console inconnue
    r = client.post("/api/packages/g/covers", headers=auth(), json={"console": "gba", "name": "Nope"}).json()
    assert r["found"] == "" and r["tried"] == "Nope"
    assert client.post("/api/packages/g/covers", headers=auth(), json={"console": "xbox"}).status_code == 400


def test_cover_name_candidates():
    from app.covers import candidates, guess_console

    assert candidates("Zelda: Link's Awakening (USA, Europe) (Rev 2) [!]") == [
        "Zelda_ Link's Awakening (USA, Europe) (Rev 2) [!]",
        "Zelda_ Link's Awakening (USA, Europe) (Rev 2)",
        "Zelda_ Link's Awakening (USA, Europe)",
        "Zelda_ Link's Awakening",
    ]
    assert guess_console(".GBA") == "gba" and guess_console(".iso") == ""


def test_spec_validation(client):
    """Règles de « Creating a Store Repository » (tico 0.8)."""
    def problems(**fields):
        client.post("/api/packages", headers=auth(), json={"id": "v", **fields})
        out = " ".join(client.get("/api/packages/v", headers=auth()).json()["problems"])
        client.delete("/api/packages/v", headers=auth())
        return out

    assert "dossier système" in problems(install="tico/modules/x")
    assert "dossier système" not in problems(install="tico/roms/nds")       # tout dossier non système
    assert "exigent un dossier" in problems(keep_archive=True)
    assert "choisissez la console" in problems(type="rom", category="game", filename="a.gba", size=10)
    assert "64 Mio" in problems(type="rom", console="gba", filename="a.gba", size=70 * 1024 * 1024)
    assert "fichier brut" in problems(type="rom", console="gba", filename="a.zip", size=10)
    assert "taille est obligatoire" in problems(type="rom", console="gba", filename="a.gba")
    assert "ajoutez la console" in problems(category="game")
    assert "Cores from any repository" in problems(type="module", category="app")
    assert "catégorie « app »" in problems(type="module", category="game")
    assert "2 tags" in problems(tags=["a", "b", "c"])
    assert "soundpacks/v" not in problems(category="soundpack")              # dossier par défaut


def test_zip_layout_from_sd_root(client):
    def upload_zip(pkg_id, names, **fields):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for n in names:
                zf.writestr(n, b"x")
        client.post("/api/packages", headers=auth(), json={"id": pkg_id, **fields})
        client.post(f"/api/packages/{pkg_id}/file", headers=auth(), files={"file": ("p.zip", buf.getvalue())})
        return " ".join(client.get(f"/api/packages/{pkg_id}", headers=auth()).json()["problems"])

    # le cas qui échouait sur la Switch : la ROM à la racine du zip, sans dossier d'installation
    assert "racine de la carte SD" in upload_zip("z1", ["Mon jeu.nds"])
    assert "racine de la carte SD" not in upload_zip("z2", ["switch/z2/z2.nro", "tico/roms/gba/Jeu.gba"])
    assert "racine de la carte SD" not in upload_zip("z3", ["Mon jeu.nds"], install="tico/roms/nds")
    assert "un seul dossier" in upload_zip("m1", ["autre/module.json"], type="module", category="app")


def test_index_follows_spec(client):
    client.post("/api/packages", headers=auth(), json={"id": "rom-gba", "name": "Jeu", "category": "game", "type": "rom"})
    client.post("/api/packages/rom-gba/file", headers=auth(), files={"file": ("Mon Jeu (USA).gba", b"r" * 100)})
    client.post("/api/packages", headers=auth(), json={
        "id": "zipped", "category": "game", "console": "snes", "keep_archive": True, "install": "tico/roms/snes"})
    client.post("/api/packages/zipped/file", headers=auth(), files={"file": ("Game.zip", b"PK")})
    client.post("/api/packages", headers=auth(), json={"id": "sounds", "category": "soundpack"})
    client.post("/api/packages/sounds/file", headers=auth(), files={"file": ("s.zip", b"PK")})
    client.post("/api/packages", headers=auth(), json={"id": "app1", "category": "homebrew", "type": "nro"})
    client.post("/api/packages/app1/file", headers=auth(), files={"file": ("a.nro", b"NRO0")})
    entries = {e["id"]: e for e in client.get("/index.json").json()["packages"]}

    # tico 0.8 masque « type: rom » : la ROM est emballée dans tico/roms/<console>/
    rom = entries["rom-gba"]
    assert rom["type"] == "zip" and rom["tags"] == ["gba"] and "install" not in rom
    assert rom["url"].endswith("/files/rom-gba/Mon%20Jeu%20%28USA%29.zip")
    data = client.get(rom["url"].replace("https://shop.test", "")).content
    assert hashlib.sha256(data).hexdigest() == rom["sha256"] and len(data) == rom["size"]
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["tico/roms/gba/Mon Jeu (USA).gba"]
        assert zf.read("tico/roms/gba/Mon Jeu (USA).gba") == b"r" * 100
    assert client.get("/api/packages/rom-gba", headers=auth()).json()["problems"] == []
    z = entries["zipped"]
    assert z["extract"] is False and z["filename"] == "Game.zip" and z["tags"] == ["snes"]
    assert entries["sounds"]["install"] == "tico/theme/soundpacks/sounds"
    app = entries["app1"]
    assert app["category"] == "app" and "install" not in app and "filename" not in app and "extract" not in app
    assert all("console" not in e for e in entries.values())


def test_covers_server_unreachable(client, monkeypatch):
    import urllib.error
    import urllib.request

    def boom(*a, **kw):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    client.post("/api/packages", headers=auth(), json={"id": "u", "category": "game", "type": "rom"})
    # l'envoi réussit même si les jaquettes sont injoignables
    r = client.post("/api/packages/u/file", headers=auth(), files={"file": ("Game (USA).gba", b"rom")})
    assert r.status_code == 200 and r.json()["console"] == "gba"
    r = client.post("/api/packages/u/covers", headers=auth(), json={})
    assert r.status_code == 502 and "injoignable" in r.json()["detail"]



def test_rom_zip_rewrapped(client, tmp_path):
    """Le cas qui échouait sur la Switch : un zip avec la ROM à sa racine."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Hermano (World).gb", b"g" * 64)
    client.post("/api/packages", headers=auth(), json={"id": "hermano", "category": "game"})
    r = client.post("/api/packages/hermano/file", headers=auth(), files={"file": ("hermano.zip", buf.getvalue())}).json()
    assert r["rom_path"] == "tico/roms/gb/Hermano (World).gb" and r["type"] == "zip" and r["console"] == "gb"
    with zipfile.ZipFile(tmp_path / "files" / r["local_file"]) as zf:
        assert zf.namelist() == ["tico/roms/gb/Hermano (World).gb"]
    assert len(list((tmp_path / "files" / "hermano").rglob("*"))) == 2  # dossier + zip final, l'original est supprimé
    assert client.get("/api/packages/hermano", headers=auth()).json()["problems"] == []
    assert client.get("/api/packages/hermano/releases", headers=auth()).json() == []


def test_rom_without_console_left_as_is(client):
    client.post("/api/packages", headers=auth(), json={"id": "iso", "category": "game"})
    r = client.post("/api/packages/iso/file", headers=auth(), files={"file": ("disc.iso", b"x")}).json()
    assert r["rom_path"] == "" and r["filename"] == "disc.iso"
    assert any("ajoutez la console" in p for p in client.get("/api/packages/iso", headers=auth()).json()["problems"])



def test_keep_archive_not_rewrapped(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Game (USA).sfc", b"s")
    client.post("/api/packages", headers=auth(), json={
        "id": "kept", "category": "game", "console": "snes", "keep_archive": True, "install": "tico/roms/snes"})
    r = client.post("/api/packages/kept/file", headers=auth(), files={"file": ("Game (USA).zip", buf.getvalue())}).json()
    assert r["rom_path"] == "" and r["filename"] == "Game (USA).zip"
    entry = client.get("/index.json").json()["packages"][0]
    assert entry["extract"] is False and entry["install"] == "tico/roms/snes"



def make_pack_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        root = "Mon Pack (pegar en raiz de SD)/"
        zf.writestr(root + "covers.jpg", b"\xff\xd8preview")
        zf.writestr(root + "tico/assets/systems/covers/gba.jpg", b"cover-gba")
        zf.writestr(root + "tico/assets/systems/covers/snes.jpg", b"cover-snes")
        zf.writestr(root + "tico/assets/systems/logos/gba.png", b"logo-gba")
        zf.writestr(root + "tico/assets/systems/symbols/gba.svg", b"<svg/>")
        zf.writestr(root + "tico/assets/backgrounds/gba/", b"")
        zf.writestr(root + "tico/theme/console_colors.json", b'{"gba": {"color_start": "000000"}}')
        zf.writestr(root + "tico/theme/soundpacks/mes_sons/click.mp3", b"mp3")
        zf.writestr(root + "README.txt", b"merci")
    return buf.getvalue()


def test_consolepack_converted(client, tmp_path):
    client.post("/api/packages", headers=auth(), json={"id": "mon-pack", "name": "Mon pack", "category": "consolepack"})
    r = client.post("/api/packages/mon-pack/file", headers=auth(), files={"file": ("pack.zip", make_pack_zip())}).json()
    pack = r["pack"]
    assert pack["consoles"] == ["gba", "snes"] and pack["counts"]["covers"] == 2 and pack["colors"]
    assert pack["soundpack_packages"] == ["mon-pack-sons"] and pack["skipped"] == ["Mon Pack (pegar en raiz de SD)/README.txt"]
    assert r["install"] == "tico/theme/consolepacks/mon-pack" and r["type"] == "zip"
    assert r["banner"].startswith("/media/") and len(r["screenshots"]) == 1
    with zipfile.ZipFile(tmp_path / "files" / r["local_file"]) as zf:
        assert sorted(zf.namelist()) == ["colors.json", "covers/gba.jpg", "covers/snes.jpg", "logos/gba.png", "symbols/gba.svg"]
        assert zf.read("covers/gba.jpg") == b"cover-gba"
    assert client.get("/api/packages/mon-pack", headers=auth()).json()["problems"] == []

    sons = client.get("/api/packages/mon-pack-sons", headers=auth()).json()
    assert sons["category"] == "soundpack" and sons["install"] == "tico/theme/soundpacks/mon-pack-sons"
    with zipfile.ZipFile(tmp_path / "files" / sons["local_file"]) as zf:
        assert zf.namelist() == ["click.mp3"]
    entry = {e["id"]: e for e in client.get("/index.json").json()["packages"]}["mon-pack"]
    assert entry["category"] == "consolepack" and entry["install"] == "tico/theme/consolepacks/mon-pack"


def test_soundpack_converted(client, tmp_path):
    client.post("/api/packages", headers=auth(), json={"id": "sons", "category": "soundpack"})
    r = client.post("/api/packages/sons/file", headers=auth(), files={"file": ("p.zip", make_pack_zip())}).json()
    with zipfile.ZipFile(tmp_path / "files" / r["local_file"]) as zf:
        assert zf.namelist() == ["click.mp3"]
    assert r["install"] == "tico/theme/soundpacks/sons"


def test_consolepack_7z(client, tmp_path):
    import shutil
    import subprocess

    if not shutil.which("bsdtar"):
        pytest.skip("bsdtar absent")
    src = tmp_path / "src"
    (src / "tico/assets/systems/covers").mkdir(parents=True)
    (src / "tico/assets/systems/covers/nes.jpg").write_bytes(b"nes")
    archive = tmp_path / "pack.7z"
    subprocess.run(["bsdtar", "--format", "7zip", "-cf", str(archive), "-C", str(src), "tico"], check=True)
    client.post("/api/packages", headers=auth(), json={"id": "p7", "category": "consolepack"})
    r = client.post("/api/packages/p7/file", headers=auth(), files={"file": ("pack.7z", archive.read_bytes())}).json()
    assert r["pack"]["consoles"] == ["nes"] and r["filename"] == "p7.zip"


def test_pack_rejects_path_traversal(tmp_path):
    from app import consolepacks

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escape.txt", b"x")
        zf.writestr("tico/assets/systems/covers/gb.jpg", b"gb")
    dest = tmp_path / "out"
    dest.mkdir()
    consolepacks.extract(evil, dest, 10**6)
    assert not (tmp_path / "escape.txt").exists() and (dest / "tico/assets/systems/covers/gb.jpg").exists()


# --- reconnaissance des ROM (No-Intro) ------------------------------------------
FAKE_DAT = """clrmamepro ( name "Nintendo - Game Boy" )
game (
	name "Tetris (World) (Rev 1)"
	rom ( name "Tetris (World) (Rev 1).gb" size 8 crc {crc} md5 00 sha1 00 )
)
game (
	name "Mario \\"Land\\" (Europe)"
	rom ( name "Mario \\"Land\\" (Europe).gb" size 3 crc 00000001 md5 00 sha1 00 )
)
"""


def tetris_dat():
    import zlib
    return FAKE_DAT.format(crc=f"{zlib.crc32(b'TETRISGB') & 0xFFFFFFFF:08X}")


def test_romnames_identify_and_clean():
    import zlib

    from app import romnames
    table = romnames.parse_dat(tetris_dat())
    assert table[f"{zlib.crc32(b'TETRISGB') & 0xFFFFFFFF:08X}"] == "Tetris (World) (Rev 1).gb"
    assert 'Mario "Land" (Europe).gb' in table.values()
    assert romnames.canonical_name(b"TETRISGB", "tetris_v11_[!].gb", table) == ("Tetris (World) (Rev 1).gb", True)
    # en-tête iNES ignoré pour le CRC
    nes = {f"{zlib.crc32(b'GAME') & 0xFFFFFFFF:08X}": "Game (USA).nes"}
    assert romnames.identify(b"H" * 16 + b"GAME", ".nes", nes) == "Game (USA).nes"
    assert romnames.clean_name("1234_-_super_mario_land_(E)_[!]_apfix") == "Super Mario Land (Europe)"
    assert romnames.clean_name("Zelda - Link's Awakening (U) [b1]") == "Zelda - Link's Awakening (USA)"
    assert romnames.clean_name("Mon Jeu (USA)") == "Mon Jeu (USA)"
    assert romnames.title_of("Tetris (World) (Rev 1).gb") == "Tetris"
    assert romnames.system_for("gameboy") == "Nintendo - Game Boy" and romnames.system_for("psx") is None


def test_rom_upload_renamed_by_crc(client, tmp_path):
    (tmp_path / "dats").mkdir()
    (tmp_path / "dats" / "Nintendo - Game Boy.dat").write_text(tetris_dat())  # cache déjà rempli
    client.post("/api/packages", headers=auth(), json={"id": "tetris", "category": "game"})
    r = client.post("/api/packages/tetris/file", headers=auth(),
                    files={"file": ("tetris_v11_[!].gb", b"TETRISGB")}).json()
    assert r["identified"] and r["rom_path"] == "tico/roms/gb/Tetris (World) (Rev 1).gb"
    assert r["name"] == "Tetris" and r["filename"] == "Tetris (World) (Rev 1).zip"
    # ROM inconnue : nom seulement nettoyé
    client.post("/api/packages", headers=auth(), json={"id": "inconnu", "category": "game"})
    r = client.post("/api/packages/inconnu/file", headers=auth(),
                    files={"file": ("super_mario_land_(E)_apfix.gb", b"???")}).json()
    assert not r["identified"] and r["rom_path"] == "tico/roms/gb/Super Mario Land (Europe).gb"


def test_rename_script(tmp_path):
    import subprocess
    import sys

    sd = tmp_path / "SD"
    gb = sd / "tico/roms/gb"
    gb.mkdir(parents=True)
    (gb / "tetris_v11.gb").write_bytes(b"TETRISGB")
    with zipfile.ZipFile(gb / "pack.zip", "w") as zf:
        zf.writestr("whatever.gb", b"TETRISGB")
    (gb / "pokemon_(F)_[!].gb").write_bytes(b"unknown")
    (gb / "notes.txt").write_text("x")
    dats = tmp_path / "dats"
    dats.mkdir()
    (dats / "Nintendo - Game Boy.dat").write_text(tetris_dat())
    script = Path(__file__).resolve().parent.parent / "scripts" / "rename-roms.py"

    dry = subprocess.run([sys.executable, str(script), str(sd), "--dats", str(dats)],
                         capture_output=True, text=True, check=True).stdout
    assert "tetris_v11.gb  ->  Tetris (World) (Rev 1).gb" in dry and (gb / "tetris_v11.gb").exists()
    out = subprocess.run([sys.executable, str(script), str(sd), "--dats", str(dats), "--apply"],
                         capture_output=True, text=True, check=True).stdout
    names = sorted(p.name for p in gb.iterdir())
    assert names == ["Pokemon (France).gb", "Tetris (World) (Rev 1).gb", "Tetris (World) (Rev 1).zip", "notes.txt"]
    assert "3 ROM, 2 reconnues par CRC, 3 renommées" in out


class FakeResponse(io.BytesIO):
    def __init__(self, data, url, filename=""):
        super().__init__(data)
        self._url = url
        import email.message
        self.headers = email.message.Message()
        if filename:
            self.headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_import_from_link_renames_rom(client, tmp_path, monkeypatch):
    import urllib.request

    (tmp_path / "dats").mkdir()
    (tmp_path / "dats" / "Nintendo - Game Boy.dat").write_text(tetris_dat())
    served = {"https://cdn.example/files/t%C3%A9tris_v11.gb": b"TETRISGB",
              "https://cdn.example/dl?id=42": make_pack_zip()}

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        if url not in served:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return FakeResponse(served[url], url, "pack.zip" if "dl?id" in url else "")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client.post("/api/packages", headers=auth(), json={
        "id": "tetris", "category": "game", "source": "url", "url": "https://cdn.example/files/t%C3%A9tris_v11.gb"})
    r = client.post("/api/packages/tetris/import", headers=auth()).json()
    assert r["source"] == "upload" and r["url"] == "" and r["identified"]
    assert r["rom_path"] == "tico/roms/gb/Tetris (World) (Rev 1).gb" and r["imported_from"].endswith("v11.gb")
    entry = client.get("/index.json").json()["packages"][0]
    assert entry["url"].startswith("https://shop.test/files/tetris/") and "install" not in entry

    # un pack via un lien de téléchargement (nom donné par Content-Disposition)
    client.post("/api/packages", headers=auth(), json={"id": "pack", "category": "consolepack"})
    r = client.post("/api/packages/pack/import", headers=auth(), json={"url": "https://cdn.example/dl?id=42"}).json()
    assert r["pack"]["consoles"] == ["gba", "snes"] and r["install"] == "tico/theme/consolepacks/pack"

    # lien mort : erreur claire, le paquet n'est pas modifié
    client.post("/api/packages", headers=auth(), json={"id": "mort", "source": "url", "url": "https://cdn.example/x.gb"})
    r = client.post("/api/packages/mort/import", headers=auth())
    assert r.status_code == 502 and "Téléchargement impossible" in r.json()["detail"]
    assert client.get("/api/packages/mort", headers=auth()).json()["source"] == "url"
    assert not list((tmp_path / "files").rglob(".download-*"))


def test_storefront_console_filter_and_shelves(client):
    for pid, name, console, tags in [("zelda", "Zelda DX", "gbc", []), ("tetris", "Tetris", "gb", []),
                                     ("hermano", "Hermano", "gb", []), ("gambatte", "Gambatte", "", ["emulator", "gb"])]:
        r = client.post("/api/packages", headers=auth(), json={
            "id": pid, "name": name, "category": "app" if pid == "gambatte" else "game", "console": console,
            "tags": tags, "source": "url", "url": f"https://example.com/{pid}.zip"})
        assert r.status_code in (200, 201), r.text
    page = client.get("/").text
    # Rayons dans l'ordre des consoles, puis les applications
    assert page.index(">Game Boy</a>") < page.index(">Game Boy Color</a>") < page.index("Applications et autres")
    assert 'href="?console=gb">Game Boy <span class="count">3</span>' in page  # 2 jeux + l'émulateur
    gb = client.get("/?console=gb").text
    assert "Hermano" in gb and "Gambatte" in gb and "Zelda DX" not in gb
    assert "3 résultat(s)" in gb
    games = client.get("/?category=game&console=gb&sort=name").text
    assert games.index("Hermano") < games.index("Tetris") and "Gambatte" not in games
    assert "Aucun résultat." in client.get("/?q=mario").text
    assert "Game Boy Color" in client.get("/p/zelda").text
