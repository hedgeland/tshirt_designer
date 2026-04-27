import asyncio, httpx, json
from config import PRINTIFY_TOKEN


async def main():
    r = httpx.get(
        "https://api.printify.com/v1/catalog/blueprints.json",
        headers={"Authorization": f"Bearer {PRINTIFY_TOKEN}"},
    )
    if r.status_code == 200:
        data = r.json()
        print(json.dumps(data[0], indent=2))


asyncio.run(main())
