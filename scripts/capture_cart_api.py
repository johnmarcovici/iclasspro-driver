#!/usr/bin/env python3
"""One-shot: log JWT cart API calls during Playwright add-to-cart (no checkout)."""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"), override=True)

from playwright.sync_api import sync_playwright

URL = (
    "https://portal.iclasspro.com/scaq/class-details/15395"
    "?selectedStudents=7268&filters=%7B%22students%22%3A%20%227268%22%2C%20%22days%22%3A%20%221%22%7D"
)
logged: list[dict] = []


def main() -> int:
    email = os.getenv("ICLASS_EMAIL", "")
    password = os.getenv("ICLASS_PASSWORD", "")
    if not email or not password:
        print("Missing credentials in .env", file=sys.stderr)
        return 1

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
            if req.method not in ("POST", "PUT", "PATCH"):
                return
            entry = {"method": req.method, "url": req.url}
            try:
                entry["body"] = req.post_data_json
            except Exception:
                entry["body"] = req.post_data
            logged.append(entry)

        page.on("request", on_request)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        if "login" in page.url.lower():
            page.goto(
                "https://portal.iclasspro.com/scaq/login?showLogin=1",
                wait_until="domcontentloaded",
            )
            page.fill("#email", email)
            page.fill("#password", password)
            page.locator("button:has-text('Log In')").first.click()
            page.wait_for_timeout(3000)
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        for text in ("Enroll Now", "Add to Cart"):
            page.get_by_role("button", name=text).first.click(timeout=30000)
            page.wait_for_timeout(2000)

        ctx.close()
        browser.close()

    out = os.path.join(_ROOT, "debug-artifacts", "captured_cart_api.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(logged, f, indent=2)
    print(f"Wrote {len(logged)} request(s) to {out}")
    for entry in logged:
        print("\n", entry["method"], entry["url"])
        print(json.dumps(entry.get("body"), indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
