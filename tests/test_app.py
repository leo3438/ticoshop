import hashlib
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TOKEN = "test-token"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TICOSHOP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TICOSHOP_PUBLIC_URL", "https://shop.test")
    monkeypatch.setenv("TICOSHOP_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("TICOSHOP_API_TOKEN", TOKEN)
    return TestClient(create_app(Settings()))


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


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
    assert pkg["url"] == "https://shop.test/files/hello/hello_world.nro"
    assert pkg["sha256"] == hashlib.sha256(payload).hexdigest()
    assert pkg["size"] == len(payload)
    assert pkg["install"] == "switch/hello"
    assert pkg["featured"] is True
    assert pkg["icon"].startswith("https://shop.test/media/")
    assert client.get("/t").json() == index

    # le fichier servi correspond au checksum publié et le compteur avance
    dl = client.get("/files/hello/hello_world.nro")
    assert dl.content == payload
    assert client.get("/api/packages/hello", headers=auth()).json()["downloads"] == 1


def test_unpublished_hidden(client):
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
    assert any("protégé" in p for p in problems)
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
    client.post("/api/packages", headers=auth(), json={"id": "ok"})
    assert client.post("/api/packages", headers=auth(), json={"id": "ok"}).status_code == 409


def test_delete_removes_files(client, tmp_path):
    client.post("/api/packages", headers=auth(), json={"id": "gone"})
    client.post("/api/packages/gone/file", headers=auth(), files={"file": ("a.zip", b"zip")})
    assert (tmp_path / "files" / "gone" / "a.zip").exists()
    assert client.delete("/api/packages/gone", headers=auth()).status_code == 204
    assert not (tmp_path / "files" / "gone").exists()


def test_public_pages_and_script(client):
    assert "index.json" in client.get("/").text
    add = client.get("/add").text
    assert "https://shop.test/index.json" in add
    assert client.get("/qr.svg").headers["content-type"].startswith("image/svg")
    script = client.get("/add-to-switch.sh").text
    assert 'INDEX_URL="${2:-https://shop.test/index.json}"' in script


def admin_login(client):
    page = client.get("/admin/login").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = client.post("/admin/login", data={"password": "secret", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    return csrf


def test_admin_flow(client):
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    csrf = admin_login(client)
    r = client.post("/admin/packages/new", data={
        "csrf": csrf, "id": "web-pkg", "name": "Web", "version": "2.0", "category": "port", "type": "zip",
        "source": "upload", "tags": "a, b", "published": "on", "extract": "on", "extra": "{}",
    }, files={"package_file": ("web.zip", b"PK..."), "screenshot_files": ("s.png", PNG)}, follow_redirects=False)
    assert r.status_code == 303, r.text
    pkg = client.get("/api/packages/web-pkg").json()
    assert pkg["tags"] == ["a", "b"] and pkg["extract"] and pkg["filename"] == "web.zip"
    assert len(pkg["screenshots"]) == 1
    assert "Web" in client.get("/admin").text

    # CSRF obligatoire
    assert client.post("/admin/packages/web-pkg/delete", data={"csrf": "x"}).status_code == 400
    client.post("/admin/packages/web-pkg/delete", data={"csrf": csrf})
    assert client.get("/api/packages/web-pkg").status_code == 404


def test_repo_metadata(client):
    client.put("/api/repo", headers=auth(), json={"name": "Shop Opens", "maintainer": "Opens"})
    index = client.get("/index.json").json()
    assert index["name"] == "Shop Opens" and index["maintainer"] == "Opens"
    json.dumps(index)
