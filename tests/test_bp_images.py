import asyncio, httpx, json
from config import PRINTIFY_TOKEN


async def main():
    r = httpx.get(
        "https://api.printify.com/v1/catalog/blueprints.json",
        headers={"Authorization": f"Bearer {PRINTIFY_TOKEN}"},
    )
    if r.status_code == 200:
        data = r.json()
        print(f"Total blueprints: {len(data)}")
        count_with_images = sum(1 for bp in data if bp.get("images") and len(bp["images"]) > 0)
        print(f"Blueprints with images: {count_with_images}")

        # Let's see the first image URL structure
        if data and data[0].get("images"):
            print(f"First image URL: {data[0]['images'][0]}")


asyncio.run(main())
