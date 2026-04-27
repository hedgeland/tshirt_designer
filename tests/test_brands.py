import asyncio, httpx, json
from config import PRINTIFY_TOKEN


async def main():
    r = httpx.get(
        "https://api.printify.com/v1/catalog/blueprints.json",
        headers={"Authorization": f"Bearer {PRINTIFY_TOKEN}"},
    )
    if r.status_code == 200:
        data = r.json()
        brands = {}
        for bp in data:
            b = bp.get("brand", "Other")
            brands[b] = brands.get(b, 0) + 1
        for b, count in sorted(brands.items(), key=lambda x: x[1], reverse=True):
            print(f"{b}: {count}")


asyncio.run(main())
