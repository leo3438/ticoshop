"""Dépôt de test minimal pour vérifier en une minute que tico accepte le format.

Ajoutez <shop>/diag/index.json dans tico : un paquet « ticoshop test » doit
apparaître, s'installer dans sdmc:/switch/ticoshop-test/ (un simple fichier
texte), puis pouvoir être désinstallé. Retirez ensuite cette source.
"""
import hashlib
import io
import zipfile
from functools import lru_cache
from typing import Any

PKG_ID = "ticoshop-test"
README = (
    "ticoshop : paquet de test.\n"
    "Si ce fichier est sur votre carte SD, le format de votre dépôt est accepté par tico.\n"
    "Vous pouvez désinstaller ce paquet depuis tico › Store › Installed.\n"
)


@lru_cache(maxsize=1)
def test_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("README.txt", date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        zf.writestr(info, README)
    return buf.getvalue()


def test_index(base_url: str) -> dict[str, Any]:
    data = test_zip()
    return {
        "schema": 1,
        "name": "ticoshop · test",
        "description": "Dépôt de diagnostic ticoshop. À retirer après le test.",
        "maintainer": "ticoshop",
        "packages": [
            {
                "id": PKG_ID,
                "name": "ticoshop test",
                "version": "1.0.0",
                "author": "ticoshop",
                "category": "homebrew",
                "type": "zip",
                "tagline": "Paquet de test : installe un fichier texte",
                "description": README,
                "url": f"{base_url}/diag/{PKG_ID}.zip",
                "filename": f"{PKG_ID}.zip",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "install": "switch/ticoshop-test",
                "extract": True,
            }
        ],
    }
