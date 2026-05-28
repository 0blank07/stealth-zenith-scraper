#!/usr/bin/env python3
"""
fix_null_colors.py

Production-ready null color fixer for Zenith.

What it does:
- Tries to load target IDs from:
  1) ZENITH_NULL_IDS_FILE env var
  2) /home/blank/null_id.txt
  3) ./null_id.txt
- If no file exists, falls back to DB query for rows where any color column is NULL
- Captures fresh Renderz API headers via Playwright
- Queries Renderz Elasticsearch in batches using assetId terms filter
- Extracts colors from animation.colors
- Updates only NULL DB color columns
- Writes unresolved IDs to null_remaining.txt
"""

import asyncio
import json
import urllib.request
import urllib.error
import psycopg2
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

# ============================================================================
# CONFIG
# ============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

API_URL = "https://renderz.app/api/search/elasticsearch"
PAGE_URL = "https://renderz.app/24/players"

LOG_FILE = LOGS_DIR / f"fix_null_colors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
REMAINING_FILE = SCRIPT_DIR / "null_remaining.txt"

NULL_IDS_FILE_CANDIDATES = [
    Path("/home/blank/null_id.txt"),
    SCRIPT_DIR / "null_id.txt",
]

env_path = None
try:
    import os
    env_value = os.getenv("ZENITH_NULL_IDS_FILE")
    if env_value:
        env_path = Path(env_value).expanduser()
except Exception:
    env_path = None

if env_path:
    NULL_IDS_FILE_CANDIDATES.insert(0, env_path)

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "zenith_data",
    "user": "zenith_bot",
    "password": "zenith6Z@"
}

BATCH_SIZE = 40
DELAY = 0.35
REQUEST_TIMEOUT = 30
MAX_RETRIES = 4
CAPTURE_WAIT_SECONDS = 20

# ============================================================================
# LOGGING
# ============================================================================

def log(message, emoji="ℹ️"):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {emoji} {message}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ============================================================================
# INPUT IDS
# ============================================================================

def load_ids_from_file(path: Path):
    ids = []
    seen = set()

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            if s.lower() in {"player_id", "asset_id"}:
                continue
            try:
                pid = int(s)
            except ValueError:
                continue
            if pid not in seen:
                seen.add(pid)
                ids.append(pid)

    return ids

def load_ids_from_db():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT player_id
            FROM player_stats
            WHERE color_position IS NULL
               OR color_rating IS NULL
               OR color_name IS NULL
               OR color_level IS NULL
            ORDER BY player_id
        """)
        ids = [int(row[0]) for row in cur.fetchall() if row[0] is not None]
        cur.close()
        return ids
    finally:
        conn.close()

def resolve_target_ids():
    for candidate in NULL_IDS_FILE_CANDIDATES:
        if candidate.exists():
            ids = load_ids_from_file(candidate)
            log(f"Loaded {len(ids):,} IDs from {candidate}", "✅")
            return ids, candidate

    ids = load_ids_from_db()
    log("No null_id.txt found, fell back to database query", "⚠️")
    log(f"Loaded {len(ids):,} IDs from database", "✅")
    return ids, None

# ============================================================================
# HEADER CAPTURE
# ============================================================================

async def capture_headers():
    log("Launching browser to capture fresh API headers", "🌐")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US"
        )

        page = await context.new_page()
        captured = []

        async def on_request(request):
            if API_URL in request.url and request.method == "POST" and not captured:
                captured.append(dict(request.headers))

        page.on("request", on_request)

        await page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)
        
        # Trigger the API by typing into the search/filter input
        if not captured:
            try:
                await page.wait_for_selector("input", timeout=8000)
                search_input = await page.query_selector("input")
                if search_input:
                    await search_input.click()
                    await asyncio.sleep(0.3)
                    await search_input.type("a", delay=150)
            except Exception:
                pass
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass

        # Wait for the API call to fire
        for _ in range(CAPTURE_WAIT_SECONDS):
            if captured:
                break
            await asyncio.sleep(1)

        await browser.close()

        if not captured:
            raise RuntimeError("Could not capture API headers from browser session")

        headers = dict(captured[0])
        headers["content-type"] = "application/json"
        log(f"Captured {len(headers)} fresh API headers", "✅")
        return headers

# ============================================================================
# API
# ============================================================================

def parse_json_maybe_double(raw_text: str):
    parsed = json.loads(raw_text)
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    return parsed

def fetch_batch(batch_ids, headers):
    payload = {
        "query": {
            "bool": {
                "must": [
                    {"terms": {"assetId": batch_ids}}
                ],
                "should": [],
                "must_not": []
            }
        },
        "sort": [
            {"assetId": {"order": "asc"}}
        ],
        "_source": [],
        "from": 0,
        "size": len(batch_ids)
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8")
        return parse_json_maybe_double(raw)

async def fetch_batch_with_retry(batch_ids, headers):
    current_headers = dict(headers)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = fetch_batch(batch_ids, current_headers)
            return data, current_headers

        except urllib.error.HTTPError as e:
            if e.code == 403 and attempt < MAX_RETRIES:
                wait = 1.5 * attempt
                log(f"HTTP 403 on batch; refreshing headers (attempt {attempt}/{MAX_RETRIES})", "⚠️")
                current_headers = await capture_headers()
                await asyncio.sleep(wait)
                continue

            log(f"HTTP error {e.code}: {e.reason}", "❌")
            return None, current_headers

        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 1.5 * attempt
                log(f"Batch request error: {e} | retrying in {wait:.1f}s", "⚠️")
                await asyncio.sleep(wait)
                continue

            log(f"Batch request failed: {e}", "❌")
            return None, current_headers

    return None, current_headers

def extract_color_map(data):
    result = {}

    if not isinstance(data, dict):
        return result

    players = data.get("players", [])
    if not isinstance(players, list):
        return result

    for player in players:
        if not isinstance(player, dict):
            continue

        asset_id = player.get("assetId")
        anim = player.get("animation", {})
        colors = anim.get("colors", {}) if isinstance(anim, dict) else {}

        if not asset_id or not isinstance(colors, dict):
            continue

        rating = colors.get("rating")
        position = colors.get("position")
        name = colors.get("name")
        level = colors.get("level")

        if any(v is not None for v in [rating, position, name, level]):
            result[int(asset_id)] = {
                "rating": rating,
                "position": position,
                "name": name,
                "level": level
            }

    return result

# ============================================================================
# DATABASE
# ============================================================================

def update_colors(conn, color_map):
    if not color_map:
        return 0

    cur = conn.cursor()
    cur.executemany(
        """
        UPDATE player_stats
        SET color_rating   = COALESCE(%s, color_rating),
            color_position = COALESCE(%s, color_position),
            color_name     = COALESCE(%s, color_name),
            color_level    = COALESCE(%s, color_level)
        WHERE player_id = %s
          AND (
                color_rating IS NULL
             OR color_position IS NULL
             OR color_name IS NULL
             OR color_level IS NULL
          )
        """,
        [
            (
                c["rating"],
                c["position"],
                c["name"],
                c["level"],
                pid
            )
            for pid, c in color_map.items()
        ]
    )
    count = cur.rowcount
    conn.commit()
    cur.close()
    return count

def save_remaining_ids(remaining_ids):
    with open(REMAINING_FILE, "w", encoding="utf-8") as f:
        for pid in sorted(remaining_ids):
            f.write(f"{pid}\n")

# ============================================================================
# MAIN
# ============================================================================

async def main():
    print("=" * 70)
    log("fix_null_colors.py - Starting", "🚀")
    print("=" * 70)

    target_ids, source_path = resolve_target_ids()

    if not target_ids:
        log("No target IDs found", "⚠️")
        return

    headers = await capture_headers()
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        batches = [target_ids[i:i + BATCH_SIZE] for i in range(0, len(target_ids), BATCH_SIZE)]
        total = len(target_ids)
        updated = 0
        api_found = set()
        failed_batches = 0

        log(f"Processing {total:,} IDs in {len(batches):,} batches of {BATCH_SIZE}", "🔄")

        for i, batch in enumerate(batches, 1):
            data, headers = await fetch_batch_with_retry(batch, headers)

            if data is None:
                failed_batches += 1
            else:
                color_map = extract_color_map(data)
                api_found.update(color_map.keys())
                count = update_colors(conn, color_map)
                updated += count

            if i % 25 == 0 or i == len(batches):
                pct = (i / len(batches)) * 100
                remaining = total - len(api_found)
                log(
                    f"Batch {i}/{len(batches)} ({pct:.1f}%) | Updated: {updated:,} | "
                    f"API found: {len(api_found):,} | Remaining unresolved: {remaining:,} | "
                    f"Failed batches: {failed_batches}",
                    "📊"
                )

            await asyncio.sleep(DELAY)

        unresolved = sorted(set(target_ids) - api_found)
        save_remaining_ids(unresolved)

        print("=" * 70)
        log(f"Done! Updated: {updated:,}", "✅")
        log(f"Unresolved IDs written to: {REMAINING_FILE}", "📂")
        log(f"Unresolved count: {len(unresolved):,}", "📂")
        if source_path:
            log(f"Input source: {source_path}", "📂")
        log(f"Log file: {LOG_FILE}", "📂")
        print("=" * 70)

    finally:
        conn.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Interrupted by user", "⚠️")
