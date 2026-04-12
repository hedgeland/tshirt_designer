"""Printify API client — catalog browsing and product publishing.

All functions are synchronous and intended to be called via asyncio.to_thread.
"""

import base64
from pathlib import Path

import httpx

_BASE = "https://api.printify.com/v1"
_TIMEOUT = 30  # seconds; image upload gets a longer timeout below


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Catalog ────────────────────────────────────────────────────────────────────

def list_shops(token: str) -> list[dict]:
    """Return all shops connected to this Printify account."""
    r = httpx.get(f"{_BASE}/shops.json", headers=_h(token), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def list_blueprints(token: str) -> list[dict]:
    """Return all product blueprints from the Printify catalog.

    Each entry has: id, title, brand, model, images.
    """
    r = httpx.get(f"{_BASE}/catalog/blueprints.json", headers=_h(token), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def list_print_providers(token: str, blueprint_id: int) -> list[dict]:
    """Return print providers available for a given blueprint."""
    url = f"{_BASE}/catalog/blueprints/{blueprint_id}/print_providers.json"
    r = httpx.get(url, headers=_h(token), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def list_variants(token: str, blueprint_id: int, provider_id: int) -> list[dict]:
    """Return all variants (color+size combos) for a blueprint+provider pair.

    Each variant has: id, title, options (dict with color/size keys), is_enabled.
    """
    url = (
        f"{_BASE}/catalog/blueprints/{blueprint_id}"
        f"/print_providers/{provider_id}/variants.json"
    )
    r = httpx.get(url, headers=_h(token), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json().get("variants", [])


# ── Publishing ─────────────────────────────────────────────────────────────────

def upload_image(token: str, image_path: str) -> str:
    """Upload a PNG file to Printify's image library. Returns the image ID."""
    path = Path(image_path)
    encoded = base64.b64encode(path.read_bytes()).decode()
    r = httpx.post(
        f"{_BASE}/uploads/images.json",
        headers=_h(token),
        json={"file_name": path.name, "contents": encoded},
        timeout=120,  # 4K PNG uploads can be large
    )
    r.raise_for_status()
    return r.json()["id"]


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
) -> str:
    """Create a Printify product draft and return its product ID."""
    payload = {
        "title": title,
        "description": description,
        "blueprint_id": blueprint_id,
        "print_provider_id": provider_id,
        "variants": [
            {"id": vid, "price": price_cents, "is_enabled": True}
            for vid in variant_ids
        ],
        # Single print area covering all variants — design centered on the front.
        "print_areas": [
            {
                "variant_ids": variant_ids,
                "placeholders": [
                    {
                        "position": "front",
                        "images": [
                            {
                                "id": image_id,
                                "x": 0.5,
                                "y": 0.5,
                                "scale": 0.8,  # 80% of print area — leaves a natural border
                                "angle": 0,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    r = httpx.post(
        f"{_BASE}/shops/{shop_id}/products.json",
        headers=_h(token),
        json=payload,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["id"]


def publish_product(token: str, shop_id: str, product_id: str) -> None:
    """Publish a draft product to the connected store."""
    r = httpx.post(
        f"{_BASE}/shops/{shop_id}/products/{product_id}/publish.json",
        headers=_h(token),
        # Tell Printify which fields to sync to the connected store.
        json={"title": True, "description": True, "images": True, "variants": True, "tags": True},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
