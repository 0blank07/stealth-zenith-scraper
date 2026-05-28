#!/usr/bin/env python3
"""
debug_header_capture.py

Diagnoses why capture_headers() fails by logging ALL network activity,
taking a screenshot, and dumping page HTML.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

PAGE_URL = "https://renderz.app/24/players"
API_URL = "https://renderz.app/api/search/elasticsearch"
CAPTURE_WAIT_SECONDS = 30
OUT_DIR = Path("./debug_output")
OUT_DIR.mkdir(exist_ok=True)

LOG_FILE = OUT_DIR / f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

async def main():
    log("=" * 60)
    log("DEBUG: Header capture diagnosis")
    log("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            # Mimic a real browser more closely
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            }
        )

        page = await context.new_page()
        all_requests = []
        api_candidates = []
        console_errors = []

        # ── Capture EVERY request ──────────────────────────────────────
        async def on_request(req):
            entry = {
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "headers": dict(req.headers),
            }
            all_requests.append(entry)

            if "renderz" in req.url or "elasticsearch" in req.url or "search" in req.url:
                log(f"  [REQUEST] {req.method} {req.url[:120]}")

            if API_URL in req.url:
                api_candidates.append(dict(req.headers))
                log(f"  ✅ TARGET API HIT: {req.url}")

        # ── Capture EVERY response ─────────────────────────────────────
        async def on_response(resp):
            if "renderz" in resp.url or "elasticsearch" in resp.url:
                log(f"  [RESPONSE] {resp.status} {resp.url[:120]}")

        # ── Console errors ─────────────────────────────────────────────
        page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)

        page.on("request", on_request)
        page.on("response", on_response)

        # ── Navigate ───────────────────────────────────────────────────
        log(f"Navigating to: {PAGE_URL}")
        try:
            response = await page.goto(
                PAGE_URL,
                wait_until="networkidle",   # wait longer than domcontentloaded
                timeout=60000
            )
            log(f"Page response status: {response.status if response else 'None'}")
            log(f"Final URL after redirect: {page.url}")
        except Exception as e:
            log(f"❌ Navigation error: {e}")

        # ── Wait for potential lazy API calls ──────────────────────────
        log(f"Waiting {CAPTURE_WAIT_SECONDS}s for API call...")
        for i in range(CAPTURE_WAIT_SECONDS):
            if api_candidates:
                log(f"✅ API headers captured after {i}s!")
                break
            await asyncio.sleep(1)

        # ── Screenshot ─────────────────────────────────────────────────
        screenshot_path = OUT_DIR / "page_screenshot.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        log(f"Screenshot saved: {screenshot_path}")

        # ── Page title & HTML snippet ──────────────────────────────────
        title = await page.title()
        log(f"Page title: '{title}'")

        html = await page.content()
        html_path = OUT_DIR / "page_content.html"
        html_path.write_text(html, encoding="utf-8")
        log(f"Full HTML saved: {html_path} ({len(html):,} bytes)")

        # Check for Cloudflare challenge keywords
        cf_indicators = ["cf-browser-verification", "cloudflare", "Just a moment", "challenge-platform", "ray id"]
        for indicator in cf_indicators:
            if indicator.lower() in html.lower():
                log(f"  🚨 CLOUDFLARE DETECTED: found '{indicator}' in HTML")

        # ── Scroll and wait (trigger lazy loads) ──────────────────────
        if not api_candidates:
            log("No API call yet — trying scroll to trigger lazy load...")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(5)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(5)

        # ── Dump all renderz-related requests ─────────────────────────
        renderz_reqs = [r for r in all_requests if "renderz" in r["url"]]
        log(f"\nTotal requests captured: {len(all_requests)}")
        log(f"Renderz-related requests: {len(renderz_reqs)}")

        req_log_path = OUT_DIR / "all_requests.json"
        with open(req_log_path, "w") as f:
            json.dump(all_requests, f, indent=2)
        log(f"All requests dumped to: {req_log_path}")

        if renderz_reqs:
            log("\nRenderz requests breakdown:")
            for r in renderz_reqs:
                log(f"  {r['method']} {r['url'][:120]}")

        if console_errors:
            log(f"\nConsole errors ({len(console_errors)}):")
            for err in console_errors[:10]:
                log(f"  {err}")

        # ── Final verdict ──────────────────────────────────────────────
        log("\n" + "=" * 60)
        if api_candidates:
            log("✅ SUCCESS: API headers were captured!")
            headers_path = OUT_DIR / "captured_headers.json"
            with open(headers_path, "w") as f:
                json.dump(api_candidates[0], f, indent=2)
            log(f"Headers saved to: {headers_path}")
        else:
            log("❌ FAILURE: API call never fired. Check:")
            log("   1. page_screenshot.png — is Cloudflare blocking?")
            log("   2. page_content.html   — what did the page actually render?")
            log("   3. all_requests.json   — are there ANY renderz.app requests?")
            log("   4. If no renderz requests at all → IP blocked or Cloudflare JS challenge")
            log("   5. If renderz requests but different URL → API_URL constant is wrong")

        await browser.close()

asyncio.run(main())
