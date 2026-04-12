"""Integration tests for FastAPI routes using TestClient.

Auth is bypassed automatically when GOOGLE_CLIENT_ID is not set (the default
in test environments), so no OAuth mocking is required.
"""

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_index_returns_200():
    response = client.get("/")
    assert response.status_code == 200
    assert "T-Shirt" in response.text


def test_index_contains_alpine_component():
    response = client.get("/")
    assert "x-data" in response.text
    assert "designer()" in response.text


def test_index_contains_config_block():
    response = client.get("/")
    assert "app-config" in response.text


def test_get_builtin_preset():
    from src.presets import BUILTIN_NAME
    import urllib.parse
    response = client.get(f"/presets/{urllib.parse.quote(BUILTIN_NAME)}")
    assert response.status_code == 200
    data = response.json()
    assert "concepts_prompt" in data
    assert "variants_prompt" in data
    assert "style_suffix" in data


def test_get_unknown_preset_returns_404():
    response = client.get("/presets/does-not-exist")
    assert response.status_code == 404


def test_save_and_delete_preset(tmp_path, monkeypatch):
    import src.presets as presets_module
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")

    # Save
    response = client.post("/presets", data={
        "name": "Test Preset",
        "concepts": "concepts tmpl",
        "variants": "variants tmpl",
        "style": "style tmpl",
    })
    assert response.status_code == 200
    assert "Test Preset" in response.json()["names"]

    # Delete
    response = client.delete("/presets/Test%20Preset")
    assert response.status_code == 200
    assert "Test Preset" not in response.json()["names"]


def test_save_preset_rejects_builtin_name():
    from src.presets import BUILTIN_NAME
    response = client.post("/presets", data={
        "name": BUILTIN_NAME,
        "concepts": "a",
        "variants": "b",
        "style": "c",
    })
    assert response.status_code == 200
    assert "error" in response.json()


def test_static_app_js_served():
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "designer" in response.text
