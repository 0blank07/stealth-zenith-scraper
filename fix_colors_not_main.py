#!/usr/bin/env python3

import asyncio
import json
import psycopg2
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

API_URL = "https://renderz.app/api/search/elasticsearch"
PAGE_URL = "https://renderz.app/24/players"

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "zenith_data",
    "user": "zenith_bot",
    "password": "zenith6Z@"
}

BATCH_SIZE = 40

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_ids():
    with open("/home/blank/null_id.txt") as f:
        return [int(x.strip()) for x in f if x.strip().isdigit()]

def build_payload(batch):
    return {
        "query": {
            "bool": {
                "must": [
                    {"terms": {"assetId": batch}}
                ]
            }
        },
        "size": len(batch)
    }

async def fetch(page, batch):
    payload = build_payload(batch)

    result = await page.evaluate(
        """async ({url, payload}) => {
            const res = await fetch(url, {
                method: "POST",
                credentials: "include",
                headers: {
                    "content-type": "application/json"
                },
                body: JSON.stringify(payload)
            });

            const text = await res.text();
            return { status: res.status, text };
        }""",
        {"url": API_URL, "payload": payload}
    )

    if result["status"] != 200:
        raise Exception(f"HTTP {result['status']}")

    return json.loads(result["text"])

def update_db(conn, data):
    cur = conn.cursor()
    count = 0

    for p in data.get("players", []):
        colors = p.get("animation", {}).get("colors", {})
        if not colors:
            continue

        cur.execute("""
        UPDATE player_stats
        SET color_rating=%s,
            color_position=%s,
            color_name=%s,
            color_level=%s
        WHERE player_id=%s
        """, (
            colors.get("rating"),
            colors.get("position"),
            colors.get("name"),
            colors.get("level"),
            p["assetId"]
        ))
        count += 1

    conn.commit()
    return count

async def main():
    log("🚀 FINAL FIXED VERSION")

    ids = load_ids()
    log(f"Loaded {len(ids)} IDs")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="./user_data",
            headless=True,
            args=["--no-sandbox"]
        )

        page = context.pages[0] if context.pages else await context.new_page()

        # IMPORTANT: keep browser alive
        await page.goto(PAGE_URL)
        await page.wait_for_timeout(5000)

        conn = psycopg2.connect(**DB_CONFIG)

        updated = 0

        for i in range(0, len(ids), BATCH_SIZE):
            batch = ids[i:i+BATCH_SIZE]

            try:
                data = await fetch(page, batch)
                updated += update_db(conn, data)

            except Exception as e:
                log(f"⚠️ Batch failed: {e}")

        conn.close()
        await context.close()

    log(f"✅ DONE. Updated {updated}")

if __name__ == "__main__":
    asyncio.run(main())
