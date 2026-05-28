#!/usr/bin/env python3
"""
stats_colors.py - Production-ready discovery for weekly_update.py

Outputs:
- players_colors.csv
- final_missing_ids.csv

Strategy:
1. Launch Playwright and capture the real browser POST headers sent to
   /api/search/elasticsearch.
2. Reuse those exact headers for direct requests.
3. Use cursor pagination via `pagination` -> `search_after`.
4. Save latest discovered players to CSV.
5. Compare asset_ids against player_stats.player_id and save missing IDs.
"""

import asyncio
import csv
import json
import os
import random
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

import psycopg2
from playwright.async_api import async_playwright
from playwright_stealth import Stealth


SCRIPT_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOGS_DIR / f"stats_colors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

API_URL = "https://renderz.app/api/search/elasticsearch"
PAGE_URL = "https://renderz.app/24/players"

CSV_OUTPUT = SCRIPT_DIR / "players_colors.csv"
MISSING_IDS_OUTPUT = SCRIPT_DIR / "final_missing_ids.csv"

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "zenith_data",
    "user": "zenith_bot",
    "password": "zenith6Z@"
}

PAGE_SIZE = int(os.getenv("ZENITH_DISCOVERY_PAGE_SIZE", "40"))
MAX_PAGES = int(os.getenv("ZENITH_DISCOVERY_MAX_PAGES", "250"))
STALE_PAGE_LIMIT = int(os.getenv("ZENITH_DISCOVERY_STALE_PAGES", "20"))
MAX_CAPTURE_WAIT = int(os.getenv("ZENITH_DISCOVERY_CAPTURE_WAIT", "20"))
REQUEST_TIMEOUT = int(os.getenv("ZENITH_DISCOVERY_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("ZENITH_DISCOVERY_RETRIES", "4"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15"
]


def log(message, emoji="ℹ️"):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {emoji} {message}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def init_csv():
    with open(CSV_OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "asset_id", "player_id", "card_name", "rating",
            "position", "team", "nation", "color"
        ])


def append_to_csv(players):
    with open(CSV_OUTPUT, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for p in players:
            writer.writerow([
                p.get("assetId"),
                p.get("playerId"),
                p.get("cardName") or p.get("commonName"),
                p.get("rating"),
                p.get("position"),
                p.get("team"),
                p.get("nation"),
                p.get("color")
            ])


def save_missing_ids(missing_ids):
    tmp = MISSING_IDS_OUTPUT.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["asset_id"])
        for asset_id in sorted(missing_ids):
            writer.writerow([asset_id])
    os.replace(tmp, MISSING_IDS_OUTPUT)


def get_existing_player_ids():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT player_id FROM player_stats")
        ids = {int(row[0]) for row in cur.fetchall() if row[0] is not None}
        cur.close()
        return ids
    finally:
        conn.close()


def parse_json_maybe_double(raw_text):
    data = json.loads(raw_text)
    if isinstance(data, str):
        data = json.loads(data)
    return data


def build_payload(cursor=None):
    payload = {
        "query": {
            "bool": {
                "must": [],
                "should": [],
                "must_not": []
            }
        },
        "sort": [
            {"added": {"order": "desc"}},
            {"assetId": {"order": "desc"}}
        ],
        "_source": [],
        "from": 0,
        "size": PAGE_SIZE
    }
    if cursor is not None:
        payload["search_after"] = cursor
    return payload


def extract_players(data):
    if not isinstance(data, dict):
        return []

    if isinstance(data.get("players"), list):
        return data["players"]

    hits = data.get("hits")
    if isinstance(hits, dict) and isinstance(hits.get("hits"), list):
        out = []
        for hit in hits["hits"]:
            if isinstance(hit, dict):
                out.append(hit.get("_source", hit))
        return out

    return []

async def capture_real_headers():
    log("Launching browser to capture real API headers", "🌐")

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )

        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )

        page = await context.new_page()

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()

        def on_request(request):
            if "elasticsearch" in request.url and request.method == "POST" and not fut.done():
                fut.set_result(dict(request.headers))

        page.on("request", on_request)

        try:
            await page.goto(PAGE_URL, wait_until="commit", timeout=60_000)

            try:
                headers = await asyncio.wait_for(asyncio.shield(fut), timeout=30)
                await browser.close()
                headers["content-type"] = "application/json"
                log(f"Captured {len(headers)} real request headers", "✅")
                return headers
            except asyncio.TimeoutError:
                log("POST not fired naturally — trying scroll trigger", "⚠️")

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(3)

            if fut.done():
                headers = fut.result()
                await browser.close()
                headers["content-type"] = "application/json"
                log(f"Captured headers via scroll trigger", "✅")
                return headers

            log("Trying hard reload", "⚠️")
            await page.reload(wait_until="commit", timeout=60_000)

            try:
                headers = await asyncio.wait_for(asyncio.shield(fut), timeout=30)
                await browser.close()
                headers["content-type"] = "application/json"
                log(f"Captured headers on reload", "✅")
                return headers
            except asyncio.TimeoutError:
                pass

            await browser.close()
            raise RuntimeError("Could not capture native browser API request")

        except RuntimeError:
            raise
        except Exception as e:
            await browser.close()
            raise RuntimeError("Could not capture native browser API request") from e

def post_api(payload, headers):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        body = resp.read().decode("utf-8")
        return parse_json_maybe_double(body)


async def fetch_page(cursor, headers):
    payload = build_payload(cursor=cursor)

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.monotonic()
        try:
            data = post_api(payload, headers)
            elapsed = time.monotonic() - start
            return data, elapsed, None

        except urllib.error.HTTPError as e:
            elapsed = time.monotonic() - start

            if e.code == 403 and attempt < MAX_RETRIES:
                wait = (2 ** attempt) + random.uniform(0.2, 0.8)
                log(f"HTTP 403 on API request | refreshing captured headers | retrying in {wait:.1f}s", "⚠️")
                headers.clear()
                headers.update(await capture_real_headers())
                await asyncio.sleep(wait)
                continue

            if e.code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                wait = (2 ** attempt) + random.uniform(0.2, 0.8)
                log(f"HTTP {e.code} on API request | retrying in {wait:.1f}s", "⚠️")
                await asyncio.sleep(wait)
                continue

            return None, elapsed, f"http_{e.code}"

        except Exception as e:
            elapsed = time.monotonic() - start
            if attempt < MAX_RETRIES:
                wait = (2 ** attempt) + random.uniform(0.2, 0.8)
                log(f"Request error: {e} | retrying in {wait:.1f}s", "⚠️")
                await asyncio.sleep(wait)
                continue
            return None, elapsed, f"error: {e}"

    return None, 0.0, "unknown_error"


async def discover():
    print("=" * 70)
    log("stats_colors.py - Discovery starting", "🚀")
    print("=" * 70)

    existing_ids = get_existing_player_ids()
    log(f"Loaded {len(existing_ids):,} existing player_ids from database", "✅")

    init_csv()
    save_missing_ids(set())

    headers = await capture_real_headers()

    total_collected = 0
    discovered_missing = set()
    stale_pages = 0
    cursor = None

    for page_num in range(1, MAX_PAGES + 1):
        data, elapsed, error = await fetch_page(cursor, headers)

        if error:
            log(f"Page {page_num} failed: {error}", "❌")
            return 1

        players = extract_players(data)
        if not players:
            log(f"No more players returned on page {page_num}", "✅")
            break

        append_to_csv(players)
        total_collected += len(players)

        page_asset_ids = {
            int(p["assetId"])
            for p in players
            if isinstance(p, dict) and p.get("assetId") is not None
        }

        new_ids = page_asset_ids - existing_ids
        discovered_missing.update(new_ids)

        if new_ids:
            stale_pages = 0
        else:
            stale_pages += 1

        log(
            f"Page {page_num}/{MAX_PAGES} | players={len(players)} | "
            f"new={len(new_ids)} | total_new={len(discovered_missing)} | "
            f"elapsed={elapsed:.2f}s",
            "📄"
        )

        cursor = data.get("pagination")
        if not cursor:
            log("No pagination cursor returned; stopping", "✅")
            break

        if stale_pages >= STALE_PAGE_LIMIT:
            log(f"Stopping early after {STALE_PAGE_LIMIT} stale pages with no new IDs", "✅")
            break

        if elapsed > 3.0:
            await asyncio.sleep(random.uniform(1.0, 2.0))
        else:
            await asyncio.sleep(random.uniform(0.2, 0.6))

    save_missing_ids(discovered_missing)

    print("=" * 70)
    log(f"Scrape complete. Collected {total_collected:,} players.", "✅")
    log(f"Discovered {len(discovered_missing):,} missing asset_ids.", "✅")
    log(f"Saved CSV: {CSV_OUTPUT}", "📂")
    log(f"Saved missing IDs: {MISSING_IDS_OUTPUT}", "📂")
    log(f"Log file: {LOG_FILE}", "📂")
    print("=" * 70)

    return 0


async def main():
    try:
        code = await discover()
        raise SystemExit(code)
    except KeyboardInterrupt:
        log("Interrupted by user", "⚠️")
        raise SystemExit(1)
    except Exception as e:
        log(f"Fatal error: {e}", "❌")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
