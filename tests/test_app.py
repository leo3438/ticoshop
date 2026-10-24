import hashlib
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

    rom = entries["rom-gba"]
    assert rom["type"] == "rom" and rom["tags"] == ["gba"] and rom["install"] == "tico/roms/gba"
    assert rom["filename"] == "Mon Jeu (USA).gba" and rom["size"] == 100
    assert rom["url"].endswith("/files/rom-gba/Mon%20Jeu%20%28USA%29.gba")
    assert client.get(rom["url"].replace("https://shop.test", "")).content == b"r" * 100
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
