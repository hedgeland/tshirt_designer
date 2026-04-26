import httpx
from config import PRINTIFY_TOKEN

h1 = {"Authorization": f"Bearer {PRINTIFY_TOKEN}"}
r1 = httpx.get("https://api.printify.com/v1/catalog/blueprints.json", headers=h1)
lm = r1.headers.get("last-modified")
print("Last-Modified:", lm)

if lm:
    h2 = {"Authorization": f"Bearer {PRINTIFY_TOKEN}", "If-Modified-Since": lm}
    r2 = httpx.get("https://api.printify.com/v1/catalog/blueprints.json", headers=h2)
    print("Status code with If-Modified-Since:", r2.status_code)
