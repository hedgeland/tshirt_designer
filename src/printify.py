"""Printify API client — catalog browsing and product publishing.

All functions are synchronous and intended to be called via asyncio.to_thread.
"""

import base64
import functools
import json
import time
from pathlib import Path
from typing import Any

import httpx

from config import (
    PRINTIFY_API_TIMEOUT,
    PRINTIFY_CACHE_DIR,
    PRINTIFY_CACHE_TTL_HOURS,
    PRINTIFY_UPLOAD_TIMEOUT,
    PRINTIFY_USE_DISK_CACHE,
)
from src.retry import with_retry

_BASE = "https://api.printify.com/v1"
_TIMEOUT = PRINTIFY_API_TIMEOUT


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Disk cache helpers ─────────────────────────────────────────────────────────

def _cache_path(*parts: object) -> Path:
    """Build an absolute path inside PRINTIFY_CACHE_DIR for the given key parts."""
    # Join parts with underscores so callers don't have to manage subdirs.
    return Path(PRINTIFY_CACHE_DIR) / ("_".join(str(p) for p in parts) + ".json")


def _cache_load(path: Path) -> Any:
    """Return deserialized cache contents if the file exists and is within TTL.

    Returns None when caching is disabled, the file is missing, or the file is
    older than PRINTIFY_CACHE_TTL_HOURS.
    """
    if not PRINTIFY_USE_DISK_CACHE:
        return None
    if not path.exists():
        return None
    # Compare file modification time against the configured TTL.
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > PRINTIFY_CACHE_TTL_HOURS:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_save(path: Path, data) -> None:
    """Write data to the disk cache as pretty-printed JSON.

    Creates the cache directory on first use. No-op when caching is disabled.
    """
    if not PRINTIFY_USE_DISK_CACHE:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Catalog ────────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def list_shops(token: str) -> list[dict]:
    """Return all shops connected to this Printify account.

    Shops are tied to the API token so they're not worth caching to disk
    (small payload, changes when the user connects/disconnects a store).
    The lru_cache is enough to avoid repeat calls within a single process.
    """
    def _call():
        r = httpx.get(f"{_BASE}/shops.json", headers=_h(token), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    return with_retry(_call)


@functools.lru_cache(maxsize=1)
def list_blueprints(token: str) -> list[dict]:
    """Return all product blueprints from the Printify catalog.

    Each entry has: id, title, brand, model, images.
    Results are persisted to disk so subsequent server starts don't need an API
    round-trip for the ~700-entry catalog.
    """
    # Check disk cache before hitting the API.
    path = _cache_path("blueprints")
    cached = _cache_load(path)
    if cached is not None:
        return cached

    def _call():
        r = httpx.get(f"{_BASE}/catalog/blueprints.json", headers=_h(token), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    result = with_retry(_call)
    _cache_save(path, result)
    return result


@functools.lru_cache(maxsize=128)
def list_print_providers(token: str, blueprint_id: int) -> list[dict]:
    """Return print providers available for a given blueprint.

    Cached per blueprint_id — provider lists change very rarely.
    """
    path = _cache_path("providers", blueprint_id)
    cached = _cache_load(path)
    if cached is not None:
        return cached

    url = f"{_BASE}/catalog/blueprints/{blueprint_id}/print_providers.json"
    def _call():
        r = httpx.get(url, headers=_h(token), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    result = with_retry(_call)
    _cache_save(path, result)
    return result


@functools.lru_cache(maxsize=512)
def list_variants(token: str, blueprint_id: int, provider_id: int) -> list[dict]:
    """Return all variants (color+size combos) for a blueprint+provider pair.

    Each variant has: id, title, options (dict with color/size keys), is_enabled.
    Cached per (blueprint_id, provider_id) — variant lists are stable catalog data.
    """
    path = _cache_path("variants", blueprint_id, provider_id)
    cached = _cache_load(path)
    if cached is not None:
        return cached

    url = (
        f"{_BASE}/catalog/blueprints/{blueprint_id}"
        f"/print_providers/{provider_id}/variants.json"
    )
    def _call():
        r = httpx.get(url, headers=_h(token), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json().get("variants", [])
    result = with_retry(_call)
    _cache_save(path, result)
    return result


@functools.lru_cache(maxsize=512)
def get_print_details(token: str, blueprint_id: int, provider_id: int) -> Any:
    """Return print area profiles for a blueprint+provider pair.

    The response contains a 'profiles' list; each profile covers a subset of
    variant_ids and has a 'first_dimension' dict with width, height, unit, and dpi.
    Most shirts have one profile for all variants; some (e.g. plus sizes) have a
    second profile with a larger print area.
    Cached per (blueprint_id, provider_id) alongside variants — equally stable.
    """
    path = _cache_path("print_details", blueprint_id, provider_id)
    cached = _cache_load(path)
    if cached is not None:
        return cached

    url = (
        f"{_BASE}/catalog/blueprints/{blueprint_id}"
        f"/print_providers/{provider_id}/print_details.json"
    )
    def _call():
        r = httpx.get(url, headers=_h(token), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    result = with_retry(_call)
    _cache_save(path, result)
    return result


@functools.lru_cache(maxsize=512)
def list_blueprint_images(token: str, blueprint_id: int, provider_id: int) -> Any:
    """Return mockup images for a blueprint+provider pair.

    Each entry has src (CDN URL), variant_ids, and position ('front', 'back', etc.).
    One image per color variant showing the blank shirt — no design composite.
    Cached per (blueprint_id, provider_id) — stable catalog data.
    """
    path = _cache_path("images", blueprint_id, provider_id)
    cached = _cache_load(path)
    if cached is not None:
        return cached

    url = (
        f"{_BASE}/catalog/blueprints/{blueprint_id}"
        f"/print_providers/{provider_id}/images.json"
    )
    def _call():
        r = httpx.get(url, headers=_h(token), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    result = with_retry(_call)
    _cache_save(path, result)
    return result


# ── Publishing ─────────────────────────────────────────────────────────────────

def upload_image(token: str, image_path: str) -> str:
    """Upload a PNG file to Printify's image library. Returns the image ID."""
    path = Path(image_path)
    encoded = base64.b64encode(path.read_bytes()).decode()
    def _call():
        r = httpx.post(
            f"{_BASE}/uploads/images.json",
            headers=_h(token),
            json={"file_name": path.name, "contents": encoded},
            timeout=PRINTIFY_UPLOAD_TIMEOUT,  # 4K PNG uploads can be large
        )
        r.raise_for_status()
        return r.json()["id"]
    return with_retry(_call)


def create_product(
    token: str,
    shop_id: str,
    title: str,
    description: str,
    blueprint_id: int,
    provider_id: int,
    image_id: str,
    variant_ids: list[int],
    price_cents: int,
    design_x: float = 0.5,
    design_y: float = 0.5,
    design_scale: float = 0.8,
    design_angle: float = 0,
) -> str:
    """Create a Printify product draft and return its product ID.

    design_x/y are the image center as fractions (0–1) of the print area.
    design_scale is the fraction of the print area width the image occupies.
    design_angle is clockwise rotation in degrees (0 = no rotation).
    """
    payload = {
        "title": title,
        "description": description,
        "blueprint_id": blueprint_id,
        "print_provider_id": provider_id,
        "variants": [
            {"id": vid, "price": price_cents, "is_enabled": True}
            for vid in variant_ids
        ],
        # Single print area covering all variants — design on the front.
        "print_areas": [
            {
                "variant_ids": variant_ids,
                "placeholders": [
                    {
                        "position": "front",
                        "images": [
                            {
                                "id": image_id,
                                "x": design_x,
                                "y": design_y,
                                "scale": design_scale,
                                "angle": design_angle,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    def _call():
        r = httpx.post(
            f"{_BASE}/shops/{shop_id}/products.json",
            headers=_h(token),
            json=payload,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["id"]
    return with_retry(_call)


def publish_product(token: str, shop_id: str, product_id: str) -> None:
    """Publish a draft product to the connected store."""
    def _call():
        r = httpx.post(
            f"{_BASE}/shops/{shop_id}/products/{product_id}/publish.json",
            headers=_h(token),
            # Tell Printify which fields to sync to the connected store.
            json={"title": True, "description": True, "images": True, "variants": True, "tags": True},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
    with_retry(_call)
