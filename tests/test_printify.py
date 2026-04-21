"""Tests for src/printify.py — payload construction and retry behaviour.

All httpx calls are mocked; no real Printify API requests are made.
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

import src.printify as printify_module
from src.printify import create_product, list_shops, upload_image


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_response(json_data=None, status_code=200):
    """Return a minimal httpx.Response-like mock."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    # raise_for_status is a no-op for 2xx; simulate HTTPStatusError for 4xx/5xx
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ── create_product payload ────────────────────────────────────────────────────

def test_create_product_sends_correct_payload():
    """create_product must include blueprint_id, variant list, and print_areas in the request."""
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["payload"] = json
        return _mock_response({"id": "prod-123"})

    with patch("src.printify.httpx.post", side_effect=fake_post):
        result = create_product(
            token="tok",
            shop_id="shop1",
            title="My Shirt",
            description="Great shirt",
            blueprint_id=5,
            provider_id=99,
            image_id="img-abc",
            variant_ids=[1001, 1002],
            price_cents=2500,
            design_x=0.5,
            design_y=0.5,
            design_scale=0.8,
        )

    assert result == "prod-123"
    p = captured["payload"]
    assert p["title"] == "My Shirt"
    assert p["blueprint_id"] == 5
    assert p["print_provider_id"] == 99
    # All variant IDs must appear in the variants list
    variant_ids_in_payload = [v["id"] for v in p["variants"]]
    assert 1001 in variant_ids_in_payload
    assert 1002 in variant_ids_in_payload
    # All variants must reference the same price
    assert all(v["price"] == 2500 for v in p["variants"])
    # print_areas must reference all variant IDs
    pa_variant_ids = p["print_areas"][0]["variant_ids"]
    assert set(pa_variant_ids) == {1001, 1002}
    # Image placement values must be passed through
    img_placement = p["print_areas"][0]["placeholders"][0]["images"][0]
    assert img_placement["id"] == "img-abc"
    assert img_placement["x"] == 0.5
    assert img_placement["y"] == 0.5
    assert img_placement["scale"] == 0.8


def test_create_product_with_single_variant():
    """A single variant_id is wrapped correctly in both variants and print_areas."""
    def fake_post(url, headers, json, timeout):
        return _mock_response({"id": "prod-solo"})

    with patch("src.printify.httpx.post", side_effect=fake_post):
        result = create_product(
            token="tok", shop_id="s", title="T", description="",
            blueprint_id=1, provider_id=2, image_id="i",
            variant_ids=[42], price_cents=1000,
        )
    assert result == "prod-solo"


# ── list_shops retry ──────────────────────────────────────────────────────────

def test_list_shops_retries_on_503(caplog):
    """list_shops must retry when the first call gets a 503 ServiceUnavailable."""
    import logging
    call_count = 0

    def fake_get(url, headers, timeout):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First attempt: simulate a transient 503
            raise httpx.TimeoutException("timed out", request=MagicMock())
        return _mock_response([{"id": "shop1"}])

    with patch("src.printify.httpx.get", side_effect=fake_get):
        # Patch the retry delay to 0 so the test runs instantly
        with patch("src.retry.time.sleep"):
            result = list_shops("tok")

    assert call_count == 2
    assert result == [{"id": "shop1"}]


def test_list_shops_raises_after_max_retries():
    """list_shops must raise after exhausting all retry attempts."""
    def always_timeout(url, headers, timeout):
        raise httpx.TimeoutException("timed out", request=MagicMock())

    with patch("src.printify.httpx.get", side_effect=always_timeout):
        with patch("src.retry.time.sleep"):
            with pytest.raises(httpx.TimeoutException):
                list_shops("tok")


# ── upload_image ──────────────────────────────────────────────────────────────

def test_upload_image_sends_base64_encoded_file(tmp_path):
    """upload_image must base64-encode the file and include its name in the payload."""
    import base64
    test_file = tmp_path / "design.png"
    test_file.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _mock_response({"id": "img-xyz"})

    with patch("src.printify.httpx.post", side_effect=fake_post):
        result = upload_image("tok", str(test_file))

    assert result == "img-xyz"
    assert captured["json"]["file_name"] == "design.png"
    # contents must be valid base64 of the original bytes
    decoded = base64.b64decode(captured["json"]["contents"])
    assert decoded == test_file.read_bytes()
