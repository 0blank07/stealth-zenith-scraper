#!/usr/bin/env python3
"""
stats_colors_replacement.py

Production-ready replacement for stats_colors.py

Design
------
1) Direct API is the default path.
2) Browser discovery is only a backup when the template is missing or stale.
3) Query expansion is used to maximize coverage.
4) Results are de-duplicated by asset_id and compared against DB player_ids.
5) Diagnostic mode helps identify whether the page, browser capture, or API is failing.

Outputs
-------
- players_colors.csv
- final_missing_ids.csv
- renderz_template.json
- logs/stats_colors_*.log

Usage
-----
python3 stats_colors_replacement.py
python3 stats_colors_replacement.py --refresh-template
python3 stats_colors_replacement.py --discover-only
python3 stats_colors_replacement.py --diagnose
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import json
import os
import random
import string
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import psycopg2
from playwright.async_api import async_playwright
from playwright_stealth import Stealth


SCRIPT_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOGS_DIR / f"stats_colors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
CSV_OUTPUT = SCRIPT_DIR / "players_colors.csv"
MISSING_IDS_OUTPUT = SCRIPT_DIR / "final_missing_ids.csv"
TEMPLATE_PATH = SCRIPT_DIR / "renderz_template.json"

API_URL = "https://renderz.app/api/search/elasticsearch"
PAGE_URL = "https://renderz.app/24/players"

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "zenith_data",
    "user": "zenith_bot",
    "password": "zenith6Z@",
}

PAGE_SIZE = int(os.getenv("ZENITH_DISCOVERY_PAGE_SIZE", "40"))
MAX_PAGES = int(os.getenv("ZENITH_DISCOVERY_MAX_PAGES", "250"))
STALE_PAGE_LIMIT = int(os.getenv("ZENITH_DISCOVERY_STALE_PAGES", "20"))
REQUEST_TIMEOUT = int(os.getenv("ZENITH_DISCOVERY_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("ZENITH_DISCOVERY_RETRIES", "4"))
TEMPLATE_TTL_HOURS = int(os.getenv("ZENITH_TEMPLATE_TTL_HOURS", "72"))
MAX_QUERIES = int(os.getenv("ZENITH_MAX_QUERIES", "300"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
]


def log(message: str, emoji: str = "ℹ️") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {emoji} {message}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def init_csv() -> None:
    with open(CSV_OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "asset_id",
            "player_id",
            "card_name",
            "rating",
            "position",
            "team",
            "nation",
            "color",
        ])


def append_to_csv(players: list[dict[str, Any]]) -> None:
    if not players:
        return
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
                p.get("color"),
            ])


def save_missing_ids(missing_ids: set[int]) -> None:
    tmp = MISSING_IDS_OUTPUT.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["asset_id"])
        for asset_id in sorted(missing_ids):
            writer.writerow([asset_id])
    os.replace(tmp, MISSING_IDS_OUTPUT)


def get_existing_player_ids() -> set[int]:
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT player_id FROM player_stats")
            return {int(row[0]) for row in cur.fetchall() if row[0] is not None}
    finally:
        conn.close()


def parse_json_maybe_double(raw_text: str) -> Any:
    data = json.loads(raw_text)
    if isinstance(data, str):
        data = json.loads(data)
    return data


def extract_players(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    players = data.get("players")
    if isinstance(players, list):
        return [p for p in players if isinstance(p, dict)]

    hits = data.get("hits")
    if isinstance(hits, dict) and isinstance(hits.get("hits"), list):
        out: list[dict[str, Any]] = []
        for hit in hits["hits"]:
            if isinstance(hit, dict):
                src = hit.get("_source", hit)
                if isinstance(src, dict):
                    out.append(src)
        return out

    return []


def normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower().strip()
        if lk in {
            "content-length",
            "host",
            "origin",
            "referer",
            "connection",
            "sec-fetch-dest",
            "sec-fetch-mode",
            "sec-fetch-site",
            "sec-fetch-user",
            "upgrade-insecure-requests",
        }:
            continue
        cleaned[k] = v
    cleaned.setdefault("content-type", "application/json")
    return cleaned


def deep_replace_strings(obj: Any, old: str, new: str) -> Any:
    if isinstance(obj, str):
        return obj.replace(old, new)
    if isinstance(obj, list):
        return [deep_replace_strings(v, old, new) for v in obj]
    if isinstance(obj, dict):
        return {k: deep_replace_strings(v, old, new) for k, v in obj.items()}
    return obj


def set_cursor_in_payload(payload: dict[str, Any], cursor: Optional[list[Any]]) -> dict[str, Any]:
    out = copy.deepcopy(payload)
    if cursor is None:
        out.pop("search_after", None)
        return out
    out["search_after"] = cursor
    return out


def inject_query_text(payload: dict[str, Any], query_text: str, sample_query: str) -> dict[str, Any]:
    out = copy.deepcopy(payload)
    q = query_text.strip()

    if not q:
        return out

    if sample_query and sample_query in json.dumps(out, ensure_ascii=False):
        return deep_replace_strings(out, sample_query, q)

    query = out.get("query")
    if isinstance(query, dict):
        bool_part = query.get("bool")
        if isinstance(bool_part, dict):
            should = bool_part.get("should")
            if isinstance(should, list) and should:
                first = should[0]
                if isinstance(first, dict):
                    if "multi_match" in first and isinstance(first["multi_match"], dict):
                        first["multi_match"]["query"] = q
                        return out
                    if "match" in first and isinstance(first["match"], dict):
                        for k in list(first["match"].keys()):
                            first["match"][k] = q
                        return out

            bool_part["should"] = [{
                "multi_match": {
                    "query": q,
                    "fields": [
                        "cardName^4",
                        "commonName^4",
                        "team^2",
                        "nation^2",
                        "position",
                    ],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                }
            }]
            bool_part["minimum_should_match"] = 1
            return out

    for key in ("q", "query", "search", "term", "text", "keyword", "keywords"):
        if key in out:
            out[key] = q
            return out

    return out


def build_query_list() -> list[str]:
    queries: list[str] = [""]
    queries.extend(list(string.ascii_lowercase))
    queries.extend(list(string.digits))

    for a in string.ascii_lowercase:
        for b in string.ascii_lowercase:
            queries.append(a + b)
            if len(queries) >= MAX_QUERIES:
                return queries[:MAX_QUERIES]

    return queries[:MAX_QUERIES]


@dataclass
class TemplateData:
    headers: dict[str, str]
    payload: dict[str, Any]
    sample_query: str
    discovered_at: str
    source_url: str = PAGE_URL
    api_url: str = API_URL
    note: str = ""


def save_template(template: TemplateData) -> None:
    data = {
        "discovered_at": template.discovered_at,
        "source_url": template.source_url,
        "api_url": template.api_url,
        "sample_query": template.sample_query,
        "headers": template.headers,
        "payload": template.payload,
        "note": template.note,
    }
    TEMPLATE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def load_template() -> Optional[TemplateData]:
    if not TEMPLATE_PATH.exists():
        return None

    try:
        raw = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        discovered_at = raw.get("discovered_at")
        headers = raw.get("headers")
        payload = raw.get("payload")
        sample_query = str(raw.get("sample_query", ""))

        if not discovered_at or not isinstance(headers, dict) or not isinstance(payload, dict):
            return None

        dt = datetime.fromisoformat(discovered_at)
        if datetime.now() - dt > timedelta(hours=TEMPLATE_TTL_HOURS):
            return None

        return TemplateData(
            headers=normalize_headers({str(k): str(v) for k, v in headers.items()}),
            payload=payload,
            sample_query=sample_query,
            discovered_at=discovered_at,
            source_url=str(raw.get("source_url", PAGE_URL)),
            api_url=str(raw.get("api_url", API_URL)),
            note=str(raw.get("note", "")),
        )
    except Exception:
        return None


def make_payload(base_payload: dict[str, Any], cursor: Optional[list[Any]], query_text: str, sample_query: str) -> dict[str, Any]:
    payload = set_cursor_in_payload(base_payload, cursor)
    payload = inject_query_text(payload, query_text, sample_query)
    if "size" not in payload:
        payload["size"] = PAGE_SIZE
    if "_source" not in payload:
        payload["_source"] = []
    return payload


def post_api(payload: dict[str, Any], headers: dict[str, str]) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        body = resp.read().decode("utf-8")
        return parse_json_maybe_double(body)


async def fetch_page(
    base_payload: dict[str, Any],
    cursor: Optional[list[Any]],
    query_text: str,
    sample_query: str,
    headers: dict[str, str],
) -> tuple[Optional[Any], float, Optional[str]]:
    payload = make_payload(base_payload, cursor, query_text, sample_query)

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.monotonic()
        try:
            data = await asyncio.to_thread(post_api, payload, headers)
            return data, time.monotonic() - start, None

        except urllib.error.HTTPError as e:
            elapsed = time.monotonic() - start
            if e.code in {403, 429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                wait = (2 ** attempt) + random.uniform(0.25, 0.9)
                log(f"HTTP {e.code} | retrying in {wait:.1f}s", "⚠️")
                await asyncio.sleep(wait)
                continue
            return None, elapsed, f"http_{e.code}"

        except Exception as e:
            elapsed = time.monotonic() - start
            if attempt < MAX_RETRIES:
                wait = (2 ** attempt) + random.uniform(0.25, 0.9)
                log(f"Request error: {e} | retrying in {wait:.1f}s", "⚠️")
                await asyncio.sleep(wait)
                continue
            return None, elapsed, f"error: {e}"

    return None, 0.0, "unknown_error"


def search_input_selectors() -> list[str]:
    return [
        'input[placeholder="Search for players..."]',
        'input[placeholder*="Search for players"]',
        'input[placeholder*="Search"]',
        'input[placeholder*="search"]',
        'input[aria-label*="Search"]',
        'input[type="search"]',
        'input[type="text"]',
    ]


async def find_visible_search_input(page):
    for selector in search_input_selectors():
        loc = page.locator(selector).first
        try:
            if await loc.count() > 0:
                await loc.wait_for(state="attached", timeout=5000)
                return loc, selector
        except Exception:
            continue
    return None, None


async def capture_template_via_browser(sample_query: str = "messi", headed: bool = False) -> TemplateData:
    log("Capturing request template via browser backup path", "🌐")

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=not headed,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        async def route_handler(route, request):
            url = request.url
            if any(domain in url for domain in [
                "posthog.renderz.app",
                "www.googletagmanager.com",
                "static.cloudflareinsights.com",
            ]):
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", route_handler)

        page = await context.new_page()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        page_errors: list[str] = []
        console_errors: list[str] = []
        failed_requests: list[str] = []

        def on_pageerror(exc):
            page_errors.append(str(exc))
            log(f"PAGE ERROR: {exc}", "❌")

        def on_console(msg):
            if msg.type in {"error", "warning"}:
                console_errors.append(msg.text)
                log(f"console[{msg.type}] {msg.text}", "🟦")

        def on_requestfailed(req):
            failed_requests.append(f"{req.method} {req.url}")
            log(f"REQUEST FAILED -> {req.method} {req.url}", "⚠️")

        def on_request(request):
            if request.method == "POST" and API_URL in request.url and not future.done():
                post_data = request.post_data or ""
                try:
                    parsed = parse_json_maybe_double(post_data) if post_data else {}
                except Exception:
                    parsed = {}
                future.set_result({
                    "headers": dict(request.headers),
                    "payload": parsed if isinstance(parsed, dict) else {},
                    "raw_post_data": post_data,
                })

        page.on("pageerror", on_pageerror)
        page.on("console", on_console)
        page.on("requestfailed", on_requestfailed)
        page.on("request", on_request)

        try:
            await page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60_000)

            try:
                await page.wait_for_load_state("networkidle", timeout=12_000)
            except Exception:
                pass

            search_loc, used_selector = await find_visible_search_input(page)
            if search_loc is not None:
                log(f"Using search selector: {used_selector}", "✅")
                try:
                    await search_loc.click(timeout=5000)
                    await search_loc.fill(sample_query, timeout=5000)
                except Exception:
                    try:
                        await search_loc.click()
                        await search_loc.fill("")  # clear first

                        for char in sample_query:
                            await search_loc.type(char, delay=random.uniform(80, 140))
                        await page.wait_for_timeout(2000)
                        await asyncio.sleep(1.5)
                        await page.keyboard.press("Enter")

                    except Exception:
                        pass
            else:
                log("Exact search selector not found; using keyboard fallback", "⚠️")
                try:
                    await page.keyboard.press("Tab")
                    await page.keyboard.type(sample_query, delay=40)
                except Exception:
                    pass

            await asyncio.sleep(1.5)

            if not future.done():
                try:
                    await page.keyboard.press("Enter")
                except Exception:
                    pass
                await asyncio.sleep(1.5)

            if not future.done():
                try:
                    await page.evaluate(
                        """
                        ((query) => {
                          const candidates = Array.from(document.querySelectorAll('input'));
                          const target = candidates.find(i => i.placeholder && i.placeholder.includes('Search for players')) || candidates[0];
                          if (!target) return false;
                          target.focus();
                          target.value = query;
                          target.dispatchEvent(new Event('input', { bubbles: true }));
                          target.dispatchEvent(new Event('change', { bubbles: true }));
                          return true;
                        })
                        """,
                        sample_query,
                    )
                except Exception:
                    pass
                await asyncio.sleep(2)

            if not future.done():
                raise RuntimeError("Could not capture the POST request from browser backup path")

            captured = future.result()
            headers = normalize_headers(captured["headers"])
            payload = captured["payload"] if isinstance(captured["payload"], dict) and captured["payload"] else {
                "query": {"bool": {"must": [], "should": [], "must_not": [], "minimum_should_match": 0}},
                "sort": [
                    {"added": {"order": "desc"}},
                    {"assetId": {"order": "desc"}},
                ],
                "_source": [],
                "from": 0,
                "size": PAGE_SIZE,
            }

            template = TemplateData(
                headers=headers,
                payload=payload,
                sample_query=sample_query,
                discovered_at=datetime.now().isoformat(timespec="seconds"),
                source_url=PAGE_URL,
                api_url=API_URL,
                note="Captured from browser backup path",
            )
            template.cookies = cookies
            save_template(template)
            log(f"Captured template with {len(headers)} headers", "✅")
            if page_errors:
                log(f"Browser page errors observed: {len(page_errors)}", "⚠️")
            if console_errors:
                log(f"Browser console warnings/errors observed: {len(console_errors)}", "⚠️")
            if failed_requests:
                log(f"Browser request failures observed: {len(failed_requests)}", "⚠️")
            return template

        finally:
            await browser.close()


async def get_template(force_refresh: bool = False) -> TemplateData:
    template = None if force_refresh else load_template()
    if template:
        log(f"Loaded cached template from {TEMPLATE_PATH.name}", "✅")
        return template
    return await capture_template_via_browser(sample_query="messi", headed=False)


async def scrape_query_space(template: TemplateData, existing_ids: set[int]) -> tuple[int, set[int]]:
    init_csv()
    save_missing_ids(set())

    queries = build_query_list()
    total_collected = 0
    discovered_missing: set[int] = set()
    written_ids: set[int] = set()

    for query_index, query_text in enumerate(queries, start=1):
        log(f"Query {query_index}/{len(queries)} -> {query_text!r}", "🔎")

        cursor: Optional[list[Any]] = None
        stale_pages = 0
        seen_cursors: set[str] = set()
        query_new = 0

        for page_num in range(1, MAX_PAGES + 1):
            data, elapsed, error = await fetch_page(
                base_payload=template.payload,
                cursor=cursor,
                query_text=query_text,
                sample_query=template.sample_query,
                headers=template.headers,
            )

            if error:
                log(f"Query {query_text!r} page {page_num} failed: {error}", "❌")
                break

            players = extract_players(data)
            if not players:
                log(f"No more players returned for query {query_text!r} on page {page_num}", "✅")
                break

            new_players: list[dict[str, Any]] = []
            for p in players:
                asset_id = p.get("assetId")
                if asset_id is None:
                    continue
                try:
                    asset_id_int = int(asset_id)
                except Exception:
                    continue

                if asset_id_int not in existing_ids:
                    discovered_missing.add(asset_id_int)

                if asset_id_int not in written_ids:
                    written_ids.add(asset_id_int)
                    new_players.append(p)

            if new_players:
                append_to_csv(new_players)
                total_collected += len(new_players)
                query_new += len(new_players)
                stale_pages = 0
            else:
                stale_pages += 1

            log(
                f"query={query_text!r} page={page_num}/{MAX_PAGES} | "
                f"players={len(players)} | new_written={len(new_players)} | "
                f"query_total_new={query_new} | elapsed={elapsed:.2f}s",
                "📄",
            )

            next_cursor = data.get("pagination") if isinstance(data, dict) else None
            if not next_cursor:
                break

            cursor_key = json.dumps(next_cursor, sort_keys=True, default=str)
            if cursor_key in seen_cursors:
                log(f"Repeated cursor detected for query {query_text!r}; stopping this query", "⚠️")
                break
            seen_cursors.add(cursor_key)

            cursor = next_cursor

            if stale_pages >= STALE_PAGE_LIMIT:
                log(f"Stopping query {query_text!r} after {STALE_PAGE_LIMIT} stale pages", "✅")
                break

            await asyncio.sleep(random.uniform(0.15, 0.5))

    save_missing_ids(discovered_missing)
    return total_collected, discovered_missing


def diagnose_page_state() -> None:
    async def _run() -> None:
        log("Running page diagnosis", "🧪")
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )

            page = await context.new_page()
            page_errors: list[str] = []
            request_hits: list[str] = []

            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.on("request", lambda req: request_hits.append(req.url))

            try:
                await page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
                await asyncio.sleep(5)

                title = await page.title()
                body = await page.locator("body").inner_text(timeout=10_000)
                api_seen = any(API_URL in u for u in request_hits)

                log(f"title: {title}", "📝")
                log(f"body snippet: {body[:500].replace(chr(10), ' ')}", "🧾")
                log(f"API request seen: {api_seen}", "📡")

                if page_errors:
                    log("Page errors detected:", "❌")
                    for err in page_errors[:10]:
                        log(err, "❌")

            finally:
                await browser.close()

    asyncio.run(_run())


async def run_pipeline(refresh_template: bool, discover_only: bool) -> int:
    print("=" * 70)
    log("stats_colors.py - production run starting", "🚀")
    print("=" * 70)

    existing_ids = get_existing_player_ids()
    log(f"Loaded {len(existing_ids):,} existing player_ids from database", "✅")

    template = await get_template(force_refresh=refresh_template)

    if discover_only:
        log("Template refreshed; exiting because --discover-only was set", "✅")
        return 0

    total_collected, discovered_missing = await scrape_query_space(template, existing_ids)

    print("=" * 70)
    log(f"Scrape complete. Collected {total_collected:,} player rows.", "✅")
    log(f"Discovered {len(discovered_missing):,} missing asset_ids.", "✅")
    log(f"Saved CSV: {CSV_OUTPUT}", "📂")
    log(f"Saved missing IDs: {MISSING_IDS_OUTPUT}", "📂")
    log(f"Template file: {TEMPLATE_PATH}", "📂")
    log(f"Log file: {LOG_FILE}", "📂")
    print("=" * 70)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Production RenderZ scraper with API-first and browser fallback.")
    parser.add_argument("--refresh-template", action="store_true", help="Refresh the cached browser template first.")
    parser.add_argument("--discover-only", action="store_true", help="Only refresh the template, do not scrape.")
    parser.add_argument("--diagnose", action="store_true", help="Run page diagnosis and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        if args.diagnose:
            diagnose_page_state()
            raise SystemExit(0)

        code = asyncio.run(run_pipeline(
            refresh_template=args.refresh_template,
            discover_only=args.discover_only,
        ))
        raise SystemExit(code)

    except KeyboardInterrupt:
        log("Interrupted by user", "⚠️")
        raise SystemExit(1)
    except Exception as e:
        log(f"Fatal error: {e}", "❌")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
