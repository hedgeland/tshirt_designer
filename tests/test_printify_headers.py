import httpx
from config import PRINTIFY_TOKEN

r = httpx.get(
    "https://api.printify.com/v1/catalog/blueprints.json",
    headers={"Authorization": f"Bearer {PRINTIFY_TOKEN}"},
)
print(f"Status: {r.status_code}")
print("Headers:")
for k, v in r.headers.items():
    if k.lower() in ("etag", "last-modified", "cache-control", "x-cache"):
        print(f"  {k}: {v}")
