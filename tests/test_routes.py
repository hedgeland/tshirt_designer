"""Integration tests for FastAPI routes using TestClient.

Auth is bypassed automatically when GOOGLE_CLIENT_ID is not set (the default
in test environments), so no OAuth mocking is required.
"""

import json

from fastapi.testclient import TestClient

from config import BRAINSTORM_SIZE, BRAINSTORM_SIZES, DEFAULT_ASPECT_RATIO, FINAL_SIZE, FINAL_SIZES
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
    import urllib.parse

    from src.presets import BUILTIN_NAME
    response = client.get(f"/presets/{urllib.parse.quote(BUILTIN_NAME)}")
    assert response.status_code == 200
    data = response.json()
    assert "concepts_prompt" in data
    assert "variants_prompt" in data
    assert "style_suffix" in data


def test_get_unknown_preset_returns_404():
    response = client.get("/presets/does-not-exist")
    assert response.status_code == 404


_C = "Give me {num_concepts} ideas for {theme}."
_V = "Create {num_variants} variants for: {concept}."
_S = "Use {bg_color} background with max {max_colors} colors."


def test_save_and_delete_preset(tmp_path, monkeypatch):
    import src.presets as presets_module
    monkeypatch.setattr(presets_module, "_PRESETS_PATH", tmp_path / "presets.json")

    # Save
    response = client.post("/presets", data={
        "name": "Test Preset",
        "concepts": _C,
        "variants": _V,
        "style": _S,
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
        "concepts": _C,
        "variants": _V,
        "style": _S,
    })
    assert response.status_code == 200
    assert "error" in response.json()


def test_static_app_js_served():
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "designer" in response.text


def test_index_config_contains_aspect_ratio_keys():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    start = html.index('id="app-config">') + len('id="app-config">')
    end = html.index("</script>", start)
    cfg = json.loads(html[start:end])
    assert "aspectRatios" in cfg
    assert len(cfg["aspectRatios"]) == 14
    assert "1:1" in cfg["aspectRatios"]
    assert "16:9" in cfg["aspectRatios"]
    assert "defaultAspectRatio" in cfg
    assert cfg["defaultAspectRatio"] == DEFAULT_ASPECT_RATIO
    assert "brainstormSizes" in cfg
    assert cfg["brainstormSizes"] == BRAINSTORM_SIZES
    assert "defaultVariantSize" in cfg
    assert cfg["defaultVariantSize"] == BRAINSTORM_SIZE
    assert "finalSizes" in cfg
    assert cfg["finalSizes"] == FINAL_SIZES
    assert "defaultFinalSize" in cfg
    assert cfg["defaultFinalSize"] == FINAL_SIZE


def test_analysis_final_returns_default_when_no_image():
    """When no final image exists in session, endpoint returns full-frame bounds."""
    response = client.get("/analysis/final", params={"session_id": "nonexistent-session"})
    assert response.status_code == 200
    data = response.json()
    assert data["content_top"] == 0.0
    assert data["content_bottom"] == 1.0


def test_separate_sessions_are_isolated():
    """State added to session A must not appear in session B."""
    sid_a = "test-session-a-isolation"
    sid_b = "test-session-b-isolation"

    # Add a column to session A only
    r = client.post("/columns", data={"session_id": sid_a})
    assert r.status_code == 200
    assert r.json()["count"] == 2

    # Session B should still have its initial single column
    r = client.get("/session/columns", params={"session_id": sid_b})
    assert r.status_code == 200
    assert len(r.json()["columns"]) == 1


def test_columns_in_same_session_are_isolated():
    """State written to column 0 must not appear in column 1 of the same session."""
    sid = "test-session-col-isolation"

    # Add a second column
    client.post("/columns", data={"session_id": sid})

    # Write selected_idx to column 0
    client.post("/session/select-variant", data={
        "session_id": sid,
        "column_id": 0,
        "selected_idx": 7,
    })

    # Column 1 must have its own independent selected_idx (None / unset)
    r = client.get("/session/columns", params={"session_id": sid})
    cols = r.json()["columns"]
    assert cols[0]["selected_idx"] == 7
    assert cols[1]["selected_idx"] is None


def test_session_num_variants_persistence():
    """Default num_variants set in one turn must survive a hard reload of the session state."""
    sid = "test-session-num-variants"

    # Set default num_variants to 3
    r = client.post("/session/num-variants", data={
        "session_id": sid,
        "num_variants": 3,
    })
    assert r.status_code == 200
    assert r.json()["num_variants"] == 3

    # Hard reload column state — session default must be 3
    r = client.get("/session/columns", params={"session_id": sid})
    assert r.status_code == 200
    assert r.json()["num_variants"] == 3
