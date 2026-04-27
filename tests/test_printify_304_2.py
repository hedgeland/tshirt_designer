import httpx
from config import PRINTIFY_TOKEN

h1 = {
    "Authorization": f"Bearer {PRINTIFY_TOKEN}",
    "If-Modified-Since": "Thu, 23 Apr 2026 20:00:00 GMT",
}
r1 = httpx.get("https://api.printify.com/v1/catalog/blueprints.json", headers=h1)
print("Yesterday Status:", r1.status_code)
print("Yesterday Last-Modified:", r1.headers.get("last-modified"))

import time

time.sleep(2)
h2 = {"Authorization": f"Bearer {PRINTIFY_TOKEN}"}
r2 = httpx.get("https://api.printify.com/v1/catalog/blueprints.json", headers=h2)
print("Current Last-Modified:", r2.headers.get("last-modified"))
