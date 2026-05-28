import asyncio, json, urllib.request
from playwright.async_api import async_playwright

API_URL = "https://renderz.app/api/search/elasticsearch"
PAGE_URL = "https://renderz.app/24/players"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = await context.new_page()
        captured_headers = None

        async def capture_request(request):
            nonlocal captured_headers
            if API_URL in request.url and request.method == "POST":
                if not captured_headers:
                    captured_headers = dict(request.headers)

        page.on("request", capture_request)
        await page.goto(PAGE_URL, wait_until='domcontentloaded', timeout=30000)
        for i in range(15):
            if captured_headers: break
            await asyncio.sleep(1)
        await browser.close()

        # Test single ID
        test_id = 19073376
        payload = {
            "query": {"bool": {"must": [{"terms": {"assetId": [test_id]}}], "should": [], "must_not": []}},
            "sort": [{"rating": {"order": "desc"}}],
            "_source": [],
            "from": 0,
            "size": 10
        }

        headers_dict = dict(captured_headers)
        headers_dict['Content-Type'] = 'application/json'
        req = urllib.request.Request(API_URL, json.dumps(payload).encode(), headers_dict, method='POST')

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            players = data.get('players', [])
            print(f"Players found: {len(players)}")
            if players:
                p = players[0]
                print(f"assetId: {p.get('assetId')}")
                print(f"cardName: {p.get('cardName')}")
                print(f"bindingXml: {p.get('bindingXml')}")
                print(f"animation.colors: {p.get('animation', {}).get('colors', {})}")
            else:
                print("❌ NOT FOUND on Renderz!")
                print(f"Full response: {json.dumps(data)[:200]}")

asyncio.run(main())
