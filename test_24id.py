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
        captured_payload = None

        async def capture_request(request):
            nonlocal captured_headers, captured_payload
            if API_URL in request.url and request.method == "POST":
                if not captured_headers:
                    captured_headers = dict(request.headers)
                    captured_payload = request.post_data

        page.on("request", capture_request)
        await page.goto(PAGE_URL, wait_until='domcontentloaded', timeout=30000)
        for i in range(15):
            if captured_headers: break
            await asyncio.sleep(1)
        await browser.close()

        print(f"✅ Headers captured: {len(captured_headers)}")
        print(f"📦 Original payload: {captured_payload}")

        # Test a null 24xxxxxx ID - grab first one from DB
        import psycopg2
        conn = psycopg2.connect(host='localhost', port=5432, database='zenith_data', user='zenith_bot', password='zenith6Z@')
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT player_id FROM player_stats WHERE (color_name IS NULL OR color_name = '') AND player_id::text LIKE '24%' LIMIT 3")
        test_ids = [row[0] for row in cur.fetchall()]
        conn.close()
        print(f"\n🔍 Testing IDs: {test_ids}")

        for test_id in test_ids:
            # Try different query formats
            for fmt_name, payload in [
                ("terms assetId", {
                    "query": {"bool": {"must": [{"terms": {"assetId": [test_id]}}], "should": [], "must_not": []}},
                    "sort": [{"rating": {"order": "desc"}}], "_source": [], "from": 0, "size": 5
                }),
                ("match assetId", {
                    "query": {"match": {"assetId": test_id}},
                    "_source": [], "from": 0, "size": 5
                }),
                ("term assetId", {
                    "query": {"term": {"assetId": test_id}},
                    "_source": [], "from": 0, "size": 5
                }),
            ]:
                headers_dict = dict(captured_headers)
                headers_dict['Content-Type'] = 'application/json'
                req = urllib.request.Request(API_URL, json.dumps(payload).encode(), headers_dict, method='POST')
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = json.loads(resp.read())
                        players = data.get('players', [])
                        print(f"  [{fmt_name}] ID {test_id}: {len(players)} players found | keys: {list(data.keys())}")
                        if players:
                            print(f"    → bindingXml: {players[0].get('bindingXml')}")
                            print(f"    → colors: {players[0].get('animation', {}).get('colors', {})}")
                            break
                except Exception as e:
                    print(f"  [{fmt_name}] Error: {e}")

asyncio.run(main())
