#!/usr/bin/env python3
"""Capture JWT promo API calls when applying a code on the portal cart."""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"), override=True)

from playwright.sync_api import sync_playwright

PORTAL = os.getenv("ICLASS_PORTAL", "scaq").strip().strip("/")
PROMO = os.getenv("ICLASS_PROMO_CODE", "").strip()
logged: list[dict] = []


def main() -> int:
    email = os.getenv("ICLASS_EMAIL", "")
    password = os.getenv("ICLASS_PASSWORD", "")
    if not email or not password:
        print("Missing credentials in .env", file=sys.stderr)
        return 1
    if not PROMO:
        print("Set ICLASS_PROMO_CODE in .env", file=sys.stderr)
        return 1

    cart_url = f"https://portal.iclasspro.com/{PORTAL}/cart"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        state = os.path.join(_ROOT, "storage_state.json")
        ctx = (
            browser.new_context(storage_state=state)
            if os.path.exists(state)
            else browser.new_context()
        )
        page = ctx.new_page()

        def on_request(req):
            if "app.iclasspro.com/api/jwt" not in req.url:
                return
            entry = {"method": req.method, "url": req.url}
            try:
                entry["body"] = req.post_data_json
            except Exception:
                entry["body"] = req.post_data
            logged.append(entry)

        page.on("request", on_request)
        page.goto(cart_url, wait_until="domcontentloaded", timeout=60000)
        if "login" in page.url.lower():
            page.goto(
                f"https://portal.iclasspro.com/{PORTAL}/login?showLogin=1",
                wait_until="domcontentloaded",
            )
            page.fill("#email", email)
            page.fill("#password", password)
            page.locator("button:has-text('Log In')").first.click()
            page.wait_for_timeout(3000)
            page.goto(cart_url, wait_until="domcontentloaded", timeout=60000)

        page.wait_for_timeout(3000)
        count = page.locator(".cart-item, [class*='cart']").count()
        print(f"Cart page loaded (rough item locators: {count})")

        promo_link = page.locator("a:has-text('Use Promo Code')")
        if promo_link.count() == 0:
            print("No 'Use Promo Code' link — cart may be empty", file=sys.stderr)
        else:
            promo_link.first.click()
            page.wait_for_timeout(1000)
            page.locator("[name='promoCode']").fill(PROMO)
            page.locator("button:has-text('Apply')").click()
            page.wait_for_timeout(4000)

        ctx.close()
        browser.close()

    out = os.path.join(_ROOT, "debug-artifacts", "captured_promo_api.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(logged, f, indent=2)

    promo_calls = [
        e
        for e in logged
        if "promo" in e.get("url", "").lower()
        or (
            isinstance(e.get("body"), dict)
            and any("promo" in str(k).lower() for k in e["body"])
        )
    ]
    print(f"Wrote {len(logged)} JWT request(s) to {out}")
    print(f"Promo-related: {len(promo_calls)}")
    for entry in promo_calls:
        url = entry.get("url", "").split("?")[0]
        print("\n", entry["method"], url)
        print(json.dumps(entry.get("body"), indent=2)[:4000])
    return 0 if promo_calls else 1


if __name__ == "__main__":
    raise SystemExit(main())
