#!/usr/bin/env python3
from __future__ import annotations

import warnings

try:
    from urllib3.exceptions import NotOpenSSLWarning

    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except ImportError:
    warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import argparse
import json
import logging
import os
import random
import re
import sys
import smtplib
import time
from datetime import datetime, timezone
from typing import Any, Optional
import pandas as pd
import yaml
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse, urljoin, quote, unquote, parse_qs

import requests

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError, sync_playwright


class EnrollmentSkipped(Exception):
    """Raised when an enrollment is intentionally skipped (e.g. already enrolled,
    already in cart). Treated as a non-failure status by the run report."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _retry(
    action,
    *,
    action_name: str,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 5.0,
    on_retry=None,
):
    """Run `action` with bounded retries and exponential backoff + jitter.

    `action` is a zero-arg callable. `on_retry`, if provided, is invoked between
    attempts with (attempt_number, error) so callers can re-prepare state
    (for example, re-resolve a stale Playwright locator)."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except EnrollmentSkipped:
            raise
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, 0.25)
            logger.warning(
                f"{action_name} failed on attempt {attempt}/{attempts}: {error}. "
                f"Retrying in {delay:.1f}s..."
            )
            if on_retry is not None:
                try:
                    on_retry(attempt, error)
                except Exception as prep_error:
                    logger.debug(
                        f"on_retry hook raised while preparing for retry: {prep_error}"
                    )
            time.sleep(delay)
    raise last_error if last_error else RuntimeError(f"{action_name} failed.")


# --- Basic Setup ---
# Load environment variables from .env file
load_dotenv(override=True)

# Regex that matches a time token like "at 10:30am" in class-card link text.
# Group 1 captures the bare time string (e.g. "10:30am").
_TIME_RE = re.compile(r"\bat\s+(\d{1,2}:\d{2}(?:am|pm))", re.IGNORECASE)

WEEK_DAYS = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]
DAY_TO_QUERY_INDEX = {name.lower(): idx for idx, name in enumerate(WEEK_DAYS, start=1)}
logger = logging.getLogger(__name__)


# --- Email Function ---
def send_log_email(
    log_file_path,
    to_addr,
    from_addr,
    app_password,
    smtp_server,
    smtp_port,
    summary_data=None,
):
    """Reads the log file, prepends a summary, and sends its content in an email."""
    try:
        with open(log_file_path, "r") as f:
            log_content = f.read()

            success_count = sum(
                1 for item in summary_data if item.get("status") == "Success"
            )
            skipped_count = sum(
                1 for item in summary_data if item.get("status") == "Skipped"
            )
            total_count = len(summary_data)
            if success_count + skipped_count == total_count:
                subject_text = f"Added {success_count} of {total_count} classes" + (
                    f" ({skipped_count} skipped)" if skipped_count else ""
                )
            else:
                subject_text = (
                    f"Enrollment Report: {success_count}/{total_count} Successful"
                    + (f", {skipped_count} skipped" if skipped_count else "")
                )

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"iClassPro: {subject_text}"
            msg["From"] = from_addr
            msg["To"] = to_addr

            text_content = f"iClassPro: {subject_text}\n\n"
            html_content = f"<h2>iClassPro: {subject_text}</h2>"

        if summary_data:
            text_content += "Summary:\n"
            html_content += (
                "<h3>Summary:</h3>"
                "<table border='1' cellpadding='5' style='border-collapse: collapse;'>"
                "<tr><th>Day</th><th>Time</th><th>Location</th><th>Status</th><th>Error</th></tr>"
            )
            for item in summary_data:
                cls = {k.lower(): v for k, v in item.get("class", {}).items()}
                status = item.get("status", "Unknown")
                error = item.get("error", "")
                day = cls.get("day", "")
                time_str = cls.get("time", "")
                location = cls.get("location", "")
                text_content += f"- {day} {time_str} at {location}: {status} {error}\n"
                html_content += f"<tr><td>{day}</td><td>{time_str}</td><td>{location}</td><td>{status}</td><td>{error}</td></tr>"

            text_content += "\n\nFull Log:\n"
            html_content += "</table><h3>Full Log:</h3>"

        text_content += log_content
        html_content += (
            f"<pre style='background: #f4f4f4; padding: 10px;'>{log_content}</pre>"
        )

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        logger.info("Connecting to SMTP server to send log email...")
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(from_addr, app_password)
            server.send_message(msg)
        logger.info("Log email sent successfully.")

    except Exception as e:
        logger.error(f"Failed to send log email: {e}")


# --- Main Class ---


def _load_locations() -> list:
    """Load the known locations list from config/locations.yaml."""
    config_path = os.path.join(os.path.dirname(__file__), "config", "locations.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f).get("locations", [])


class IClassPro:
    # Known location names used for both UI dropdowns and class-name extraction.
    KNOWN_LOCATIONS = _load_locations()

    def __init__(
        self,
        base_url: str = "",
        save_screenshots: bool = False,
        storage_state_path: str = "",
        deep_debug: bool = False,
    ):
        self.base_url = base_url
        self.save_screenshots = save_screenshots
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._last_cart_response = None
        self._login_email = ""
        self._login_password = ""
        self._is_shutting_down = False
        self._storage_state_path = (
            storage_state_path
            or os.getenv("ICLASS_STORAGE_STATE", "").strip()
            or "storage_state.json"
        )
        self._loaded_storage_state = False
        self._deep_debug = deep_debug or os.getenv(
            "ICLASS_DEEP_DEBUG", "0"
        ).lower() in (
            "1",
            "true",
            "yes",
        )
        self._debug_artifacts_dir = os.getenv("ICLASS_DEBUG_DIR", "debug-artifacts")
        self._class_detail_timeout_ms = int(
            os.getenv("ICLASS_CLASS_DETAIL_TIMEOUT_MS", "45000")
        )

    def take_screenshot(self, filename: str, full_page: bool = False):
        """Helper to save a screenshot if --save-screenshots is enabled."""
        if self.save_screenshots and self.page and not self.page.is_closed():
            os.makedirs("screenshots", exist_ok=True)
            self.page.screenshot(
                path=os.path.join("screenshots", filename), full_page=full_page
            )

    def _write_debug_artifacts(self, label: str, payload: dict) -> None:
        """Write deep-debug artifacts for failed interactions."""
        if not self._deep_debug or not self.page or self.page.is_closed():
            return
        try:
            os.makedirs(self._debug_artifacts_dir, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            base = f"{label}_{stamp}"
            html_path = os.path.join(self._debug_artifacts_dir, f"{base}.html")
            png_path = os.path.join(self._debug_artifacts_dir, f"{base}.png")
            json_path = os.path.join(self._debug_artifacts_dir, f"{base}.json")

            try:
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(self.page.content())
            except Exception as html_err:
                logger.debug(f"Failed to write debug HTML artifact: {html_err}")

            try:
                self.page.screenshot(path=png_path, full_page=True)
            except Exception as shot_err:
                logger.debug(f"Failed to write debug screenshot artifact: {shot_err}")

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info(
                f"Deep debug artifacts written under {self._debug_artifacts_dir} with prefix {base}."
            )
        except Exception as e:
            logger.warning(f"Failed to write deep debug artifacts: {e}")

    @staticmethod
    def _is_transient_browser_error(error: Exception) -> bool:
        message = str(error)
        return any(
            marker in message
            for marker in (
                "Target crashed",
                "Page crashed",
                "Target page, context or browser has been closed",
                "Browser has been closed",
            )
        )

    def _attach_debug_handlers(self) -> None:
        if self.browser:
            self.browser.on(
                "disconnected",
                lambda: (
                    logger.warning("Browser disconnected unexpectedly.")
                    if not self._is_shutting_down
                    else None
                ),
            )
        if self.page:
            self.page.on(
                "crash",
                lambda: (
                    logger.warning("Page crashed unexpectedly.")
                    if not self._is_shutting_down
                    else None
                ),
            )
            self.page.on(
                "close",
                lambda: (
                    logger.warning("Page closed unexpectedly.")
                    if not self._is_shutting_down
                    else None
                ),
            )

    def _restart_browser(self) -> None:
        logger.warning("Restarting browser after unexpected Playwright failure...")
        self.close()
        self.webdriver()

    def _goto(self, url: str, description: str = "page") -> None:
        for attempt in range(2):
            try:
                if not self.page or self.page.is_closed():
                    self.webdriver()
                self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
                return
            except PlaywrightError as error:
                if attempt == 0 and self._is_transient_browser_error(error):
                    logger.warning(
                        f"Browser became unstable while opening {description}; retrying once."
                    )
                    self._restart_browser()
                    if (
                        self._login_email
                        and self._login_password
                        and "login?showLogin=1" not in url
                    ):
                        self._login_impl(self._login_email, self._login_password)
                    continue
                raise

    def webdriver(self) -> None:
        """Initialize Playwright and launch a browser instance."""
        self.playwright = sync_playwright().start()
        headless = os.getenv("ICLASS_HEADLESS", "1").lower() not in ("0", "false", "no")
        slow_mo = int(os.getenv("ICLASS_SLOW_MO", "0"))

        logger.info(f"Launching browser (headless={headless}, slow_mo={slow_mo}ms)")
        self.browser = self.playwright.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--use-gl=swiftshader",
                "--enable-unsafe-swiftshader",
                "--disable-features=VizDisplayCompositor",
            ],
        )
        context_kwargs = {"viewport": {"width": 1280, "height": 800}}
        if self._storage_state_path and os.path.exists(self._storage_state_path):
            try:
                context_kwargs["storage_state"] = self._storage_state_path
                self._loaded_storage_state = True
                logger.info(
                    f"Loading saved session state from {self._storage_state_path}."
                )
            except Exception as state_err:
                logger.warning(f"Could not load storage state: {state_err}")
                self._loaded_storage_state = False
        self.context = self.browser.new_context(**context_kwargs)
        self.context.set_default_timeout(20000)
        self.context.set_default_navigation_timeout(45000)
        self.page = self.context.new_page()
        self._attach_debug_handlers()
        logger.info("Browser launched successfully.")

    def _save_storage_state(self) -> None:
        """Persist Playwright session cookies/local storage for later runs."""
        if not self.context or not self._storage_state_path:
            return
        try:
            self.context.storage_state(path=self._storage_state_path)
            logger.info(f"Saved session state to {self._storage_state_path}.")
        except Exception as e:
            logger.debug(f"Failed to save storage state: {e}")

    def _is_logged_in(self, timeout: int = 5000) -> bool:
        """Best-effort check for an authenticated session on the current page."""
        try:
            self.page.wait_for_timeout(200)
            # Guest pages often show "My Account" but still expose
            # Register/Log In actions. Prefer explicit account-state signals.
            try:
                if self.page.get_by_role(
                    "link", name=re.compile(r"Log Out", re.IGNORECASE)
                ).first.is_visible():
                    return True
            except Exception:
                pass
            if self._has_visible_guest_auth_links():
                return False
            self.page.locator("text=/My Account/i").first.wait_for(
                state="visible", timeout=timeout
            )
            return True
        except Exception:
            return False

    def _has_visible_guest_auth_links(self) -> bool:
        """Return True when guest navigation links are visibly present."""
        try:
            return bool(
                self.page.evaluate(
                    r"""() => {
                        const isVisible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                            const rect = el.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0;
                        };
                        const links = Array.from(document.querySelectorAll('a, button'));
                        let registerVisible = false;
                        let loginVisible = false;
                        for (const el of links) {
                            if (!isVisible(el)) continue;
                            const txt = (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
                            if (!txt) continue;
                            if (txt === 'register' || txt === 'register now') registerVisible = true;
                            if (txt === 'log in' || txt === 'login') loginVisible = true;
                        }
                        return registerVisible && loginVisible;
                    }"""
                )
            )
        except Exception:
            return False

    def _get_cart_item_count_dom_selectors(self) -> int:
        """Line-item-style cart rows (mini-cart drawer, cart page, etc.)."""
        selectors = [
            ".products-wrap .list-group-item",
            ".cart-item",
            ".cartItem",
            ".cart__item",
            "[role='listitem']",
            # iClassPro / Angular-style cart tables and lists
            "table.table-striped tbody tr",
            "table.cart tbody tr",
            ".shopping-cart tbody tr",
            "[class*='cart-item']",
            "[class*='shopping-cart'] tbody tr",
        ]
        best = 0
        for sel in selectors:
            try:
                count = self.page.locator(sel).count()
                if count > best:
                    logger.debug(f"Cart line selector '{sel}' matched {count} row(s).")
                    best = count
            except Exception:
                continue
        return best

    def _get_cart_item_count_from_evaluate(self) -> int:
        """Best-effort count from header badges and cart links (SPA often hides line items)."""
        try:
            return int(
                self.page.evaluate(
                    r"""() => {
                        let best = 0;
                        const parseIntLoose = (s) => {
                            const m = String(s || '').replace(/,/g, '').match(/\d+/);
                            return m ? parseInt(m[0], 10) : 0;
                        };

                        const bumpFromBadge = (root) => {
                            if (!root) return;
                            const badges = root.querySelectorAll(
                                '.badge, .notification-badge, [class*="badge"], [class*="cart-count"], [data-count]'
                            );
                            for (const b of badges) {
                                const n = parseIntLoose(b.textContent || '');
                                if (n > best) best = n;
                            }
                        };

                        const cartRoots = Array.from(
                            document.querySelectorAll(
                                'a[href*="/cart"], a[href*="cart"], [href*="shopping-cart"], [class*="mini-cart"], [class*="MiniCart"], [aria-label*="cart" i]'
                            )
                        );
                        for (const el of cartRoots) {
                            bumpFromBadge(el);
                            const t = (el.textContent || '').replace(/\s+/g, ' ');
                            const paren = t.match(/\((\d+)\)/);
                            if (paren) {
                                const n = parseInt(paren[1], 10);
                                if (n > best) best = n;
                            }
                        }

                        const lines = document.querySelectorAll(
                            '.products-wrap .list-group-item, .cart-item, .cartItem, table.table-striped tbody tr, table.cart tbody tr'
                        );
                        if (lines.length > best) best = lines.length;

                        return best;
                    }"""
                )
                or 0
            )
        except Exception as e:
            logger.debug(f"Cart count evaluate() failed: {e}")
            return 0

    def _get_cart_item_count(self) -> int:
        """Estimated cart quantity from visible line items and header/cart badges."""
        try:
            return max(
                self._get_cart_item_count_dom_selectors(),
                self._get_cart_item_count_from_evaluate(),
            )
        except Exception as e:
            logger.warning(f"Could not get cart item count from DOM: {e}")
            return 0

    def _cart_add_success_indicated(self) -> bool:
        """True when the portal shows a post-add confirmation (cart count may lag)."""
        try:
            page_text = self.page.locator("body").inner_text(timeout=4000)
        except Exception:
            return False
        lowered = " ".join(page_text.split()).lower()
        phrases = (
            "added to your cart",
            "added to cart",
            "successfully added",
            "has been added to your cart",
            "item has been added",
            "class has been added",
            "registration added",
            "added to the cart",
        )
        return any(p in lowered for p in phrases)

    def _wait_for_cart_item_count(
        self, min_count: int = 1, timeout: int = 60000
    ) -> int:
        """Wait until the cart appears to have at least `min_count` items."""
        logger.info(f"Waiting for cart to contain at least {min_count} item(s)...")
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            count = self._get_cart_item_count()
            if count >= min_count:
                logger.info(f"Cart item count is now {count}.")
                return count
            time.sleep(0.5)
        logger.warning("Timeout reached while waiting for cart item count.")
        return 0

    def _wait_for_cart_add_confirmation(
        self, initial_count: int, *, timeout_ms: int = 90000
    ) -> tuple[bool, int, str]:
        """Wait for line-item count increase or explicit add-to-cart success copy.

        Returns ``(ok, last_observed_count, reason)`` where reason is
        ``cart_items``, ``success_message``, or ``timeout``.
        """
        logger.info(
            "Waiting for cart confirmation (items or success message)..."
        )
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            if self._cart_add_success_indicated():
                n = self._get_cart_item_count()
                logger.info("Detected add-to-cart success message in page text.")
                return True, max(initial_count + 1, n), "success_message"
            n = self._get_cart_item_count()
            if n >= initial_count + 1:
                logger.info(f"Cart item count is now {n}.")
                return True, n, "cart_items"
            time.sleep(0.45)
        n = self._get_cart_item_count()
        return False, n, "timeout"

    def _wait_for_login_ui(self, timeout: int = 15000) -> str:
        """Wait until the login form or a portal modal becomes interactable."""
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            try:
                if self.page.locator("#email").is_visible():
                    return "credentials"
            except Exception:
                pass

            try:
                if self.page.locator("span:has-text('SCAQ')").first.is_visible():
                    return "location"
            except Exception:
                pass

            try:
                if self.page.locator("button:has-text('Yes')").first.is_visible():
                    return "customer"
            except Exception:
                pass

            self.page.wait_for_timeout(500)

        return ""

    def _login_impl(self, email: str = "", password: str = "") -> None:
        logger.info("Navigating to login page...")
        login_url = self.base_url.rstrip("/") + "/login?showLogin=1"
        self._goto(login_url, description="login page")
        logger.info("Login page loaded. Waiting for portal prompts...")
        ui_state = self._wait_for_login_ui(timeout=15000)
        if not ui_state:
            logger.warning(
                "Login UI is taking longer than expected; proceeding with direct field lookup."
            )
        self.take_screenshot("01_after_goto_login.png")

        # Handle "Select a location" modal if it appears
        try:
            location_button = self.page.locator("span:has-text('SCAQ')").first
            if location_button.is_visible():
                logger.info("Selecting location 'SCAQ'.")
                location_button.click()
                self.page.wait_for_load_state("domcontentloaded")
                self._wait_for_login_ui(timeout=8000)
        except Exception as e:
            logger.debug(f"Location selection modal not found, proceeding. Error: {e}")

        # Handle "Are you a current customer?" modal
        try:
            yes_button = self.page.locator("button:has-text('Yes')").first
            if yes_button.is_visible():
                logger.info("Clicking 'Yes' on 'current customer' modal.")
                self.take_screenshot("02_before_clicking_yes.png")
                yes_button.click()
                self.page.wait_for_timeout(1000)
                self.take_screenshot("03_after_clicking_yes.png")
        except Exception:
            logger.debug("'Current customer' modal not found, proceeding.")

        # Fill credentials
        logger.info("Entering login credentials.")
        self.take_screenshot("04_before_login_attempt.png")
        email_field = self.page.locator("#email")
        password_field = self.page.locator("#password")
        email_field.wait_for(state="visible", timeout=15000)
        password_field.wait_for(state="visible", timeout=15000)
        email_field.fill(email)
        password_field.fill(password)
        self.take_screenshot("05_after_filling_credentials.png")
        password_field.press("Enter")

        # Wait for successful authenticated state.
        deadline = time.time() + 20
        while time.time() < deadline:
            if self._is_logged_in(timeout=1000):
                logger.info("Login successful.")
                break
            self.page.wait_for_timeout(400)
        else:
            logger.error("Login failed. Could not confirm authenticated session.")
            self.take_screenshot("login_failure.png")
            raise RuntimeError("Login failed. Please check credentials and portal URL.")

    def login(self, email: str = "", password: str = "") -> None:
        """Logs into the iClassPro portal."""
        self._login_email = email
        self._login_password = password

        if self._loaded_storage_state:
            try:
                self._goto(self.base_url, description="portal home (session reuse)")
                if self._is_logged_in(timeout=4000):
                    logger.info("Reused saved session; skipping interactive login.")
                    return
                logger.info("Saved session not authenticated; performing full login.")
            except Exception as e:
                logger.debug(f"Session reuse check failed: {e}")

        for attempt in range(2):
            try:
                self._login_impl(email=email, password=password)
                self._save_storage_state()
                return
            except PlaywrightError as error:
                if attempt == 0 and self._is_transient_browser_error(error):
                    logger.warning(
                        "Browser crashed during login; relaunching and retrying once."
                    )
                    self._restart_browser()
                    continue
                raise

    def _wait_for_class_detail_ready(self, timeout: Optional[int] = None) -> None:
        """Wait until the class-details view is rendered and actionable.

        Do **not** treat ``/class-details/`` in the URL alone as ready: the SPA
        often paints the shell before enroll controls hydrate. Wait for Enroll /
        Add to Cart / detail chrome instead.
        """
        timeout = timeout or self._class_detail_timeout_ms
        deadline = time.time() + timeout / 1000.0
        last_error = None
        enroll_pattern = re.compile(r"Enroll Now!?", re.IGNORECASE)
        add_pattern = re.compile(r"Add to Cart", re.IGNORECASE)
        detail_text_re = "text=/Class Details|Sessions:|Available for/i"
        while time.time() < deadline:
            try:
                if "/class-details/" not in (self.page.url or ""):
                    self.page.wait_for_timeout(400)
                    continue
                if self.page.get_by_role(
                    "button", name=enroll_pattern
                ).first.is_visible():
                    return
                if self.page.get_by_role(
                    "button", name=add_pattern
                ).first.is_visible():
                    return
                if self.page.locator(detail_text_re).first.is_visible():
                    return
            except Exception as e:
                last_error = e
            self.page.wait_for_timeout(400)
        raise RuntimeError(
            f"Class detail view not ready within {timeout}ms"
            + (f": {last_error}" if last_error else ".")
        )

    def _wait_for_portal_idle(self, timeout: int = 45000) -> None:
        """Wait for customer-portal loading overlays/modals to settle.

        iClassPro frequently keeps cards visible but non-interactive while
        loading spinners/backdrops are on top of the UI.
        """
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            try:
                settled = self.page.evaluate(
                    r"""() => {
                        const isVisible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            return (
                                style.display !== 'none' &&
                                style.visibility !== 'hidden' &&
                                parseFloat(style.opacity || '1') > 0 &&
                                (el.offsetWidth > 0 || el.offsetHeight > 0)
                            );
                        };

                        const blockingSelectors = [
                            '.modal-backdrop.show',
                            '.modal-backdrop.fade.show',
                            '.modal.show .loading-icon',
                            '.loading-icon',
                        ];
                        for (const sel of blockingSelectors) {
                            const nodes = Array.from(document.querySelectorAll(sel));
                            if (nodes.some(isVisible)) return false;
                        }
                        return true;
                    }"""
                )
                if settled:
                    return
            except Exception:
                pass
            self.page.wait_for_timeout(400)

    def _wait_for_post_enroll_before_add_to_cart(
        self,
        add_to_cart_pattern,
        *,
        timeout_ms: Optional[int] = None,
    ) -> None:
        """After Enroll Now, wait for loading overlays to clear and for an Add to
        Cart control to become visible — no fixed sleep; readiness is inferred from
        the DOM (same idea as waiting for the page to finish rendering)."""
        timeout_ms = timeout_ms or self._class_detail_timeout_ms
        deadline = time.time() + timeout_ms / 1000.0
        logger.info("Waiting for Add to Cart control after Enroll Now...")
        while time.time() < deadline:
            self._wait_for_portal_idle(timeout=min(12000, timeout_ms))
            try:
                b = self.page.get_by_role(
                    "button", name=add_to_cart_pattern
                ).first
                if b.count() > 0 and b.is_visible():
                    logger.info("Add to Cart is visible (role=button).")
                    return
            except Exception:
                pass
            try:
                loc = self.page.locator("button:has-text('Add to Cart')").first
                if loc.count() > 0 and loc.is_visible():
                    logger.info("Add to Cart is visible (button text).")
                    return
            except Exception:
                pass
            try:
                loc = self.page.locator("a:has-text('Add to Cart')").first
                if loc.count() > 0 and loc.is_visible():
                    logger.info("Add to Cart is visible (link).")
                    return
            except Exception:
                pass
            self.page.wait_for_timeout(450)

        raise RuntimeError(
            f"Add to Cart did not become visible within {timeout_ms} ms after Enroll Now."
        )

    def _detect_idempotency_state(self) -> str:
        """Return a label if the class detail page indicates the class is
        already enrolled or already in cart. Empty string otherwise."""
        try:
            page_text = self.page.locator("body").inner_text(timeout=2500)
        except Exception:
            return ""
        normalized = " ".join(page_text.split()).lower()
        if "already enrolled" in normalized:
            return "already_enrolled"
        if "already in cart" in normalized or "already in your cart" in normalized:
            return "already_in_cart"
        return ""

    def _get_enrollment_issue(self) -> str:
        """Return a human-readable enrollment issue if the portal is showing one."""
        try:
            page_text = self.page.locator("body").inner_text(timeout=5000)
        except Exception:
            return ""

        normalized_text = " ".join(page_text.split())
        patterns = [
            r"There is a conflicting enrollment for this student\.",
            r"already enrolled[^.]*\.",
            r"already in (?:your )?cart[^.]*\.",
            r"already enrolled",
            r"already in (?:your )?cart",
            r"class is full[^.]*\.",
            r"unable to enroll[^.]*\.",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized_text, re.IGNORECASE)
            if match:
                return match.group(0)
        lowered = normalized_text.lower()
        if "already enrolled" in lowered:
            return "Already enrolled."
        if "already in your cart" in lowered or "already in cart" in lowered:
            return "Already in cart."
        return ""

    def enroll(
        self,
        location: str,
        timestr: str,
        daystr: str,
        student_id: int,
        class_index: int,
        class_url: str = "",
    ) -> None:
        """Finds and adds a single class to the cart.

        Uses schedule URL when present, otherwise resolves from search page.
        """
        schedule_url = (class_url or "").strip()
        if "/class-details/" in schedule_url:
            logger.info(f"Using class details URL from schedule: {schedule_url}")
            self._goto(schedule_url, description="class detail page")
        else:
            self._open_class_detail_page(
                location=location,
                timestr=timestr,
                daystr=daystr,
                student_id=student_id,
                class_index=class_index,
            )
        self._wait_for_class_detail_ready()
        self._add_current_class_to_cart(class_index=class_index)

    def _add_current_class_to_cart(self, class_index: int) -> None:
        """Shared 'Enroll Now' -> 'Add to Cart' flow, with retries and
        idempotency guards."""
        idem_state = self._detect_idempotency_state()
        if idem_state == "already_enrolled":
            raise EnrollmentSkipped("Already enrolled in this class.")
        if idem_state == "already_in_cart":
            raise EnrollmentSkipped("Class is already in the cart.")

        enroll_pattern = re.compile(r"Enroll Now!?|Enroll", re.IGNORECASE)
        add_to_cart_pattern = re.compile(r"Add to Cart", re.IGNORECASE)

        # Detail pages can render headers quickly while action controls lag
        # behind significantly.
        controls_deadline = time.time() + self._class_detail_timeout_ms / 1000.0
        while time.time() < controls_deadline:
            try:
                if self.page.get_by_role(
                    "button", name=re.compile(r"Select Students", re.IGNORECASE)
                ).first.is_visible():
                    break
            except Exception:
                pass
            try:
                if self.page.get_by_role(
                    "button", name=enroll_pattern
                ).first.is_visible():
                    break
            except Exception:
                pass
            try:
                if self.page.get_by_role(
                    "button", name=add_to_cart_pattern
                ).first.is_visible():
                    break
            except Exception:
                pass
            self.page.wait_for_timeout(400)

        def dismiss_non_enrollment_modals() -> None:
            # Dismiss common marketing/system dialogs that can block controls.
            dismiss_texts = [
                "Maybe Later",
                "Close",
                "No",
                "Great!",
                "Cancel",
                "Got It!",
            ]
            for text in dismiss_texts:
                try:
                    btn = self.page.get_by_role(
                        "button",
                        name=re.compile(rf"^{re.escape(text)}$", re.IGNORECASE),
                    ).first
                    if btn.is_visible():
                        btn.click(timeout=5000)
                        self.page.wait_for_timeout(400)
                except Exception:
                    pass
            # Some portals show a persistent welcome modal after login that
            # intercepts all pointer events until explicitly dismissed.
            try:
                welcome_modal = self.page.locator(
                    ".modal-welcome.show, .modal-welcome.fade.show"
                ).first
                if welcome_modal.count() > 0 and welcome_modal.is_visible():
                    for selector in [
                        ".modal-welcome.show button:has-text('Got It!')",
                        ".modal-welcome.show button:has-text('Close')",
                        ".modal-welcome.show .close",
                    ]:
                        try:
                            ctl = self.page.locator(selector).first
                            if ctl.count() > 0 and ctl.is_visible():
                                ctl.click(timeout=5000)
                                self.page.wait_for_timeout(350)
                                break
                        except Exception:
                            pass
            except Exception:
                pass

        def ensure_student_selected() -> None:
            # Some class detail pages require explicit student selection before
            # showing Enroll/Add controls, even with selectedStudents in URL.
            opened_modal = False
            detail_url_before_select = self.page.url if self.page else ""
            try:
                triggers = [
                    self.page.get_by_role(
                        "button",
                        name=re.compile(r"Select Students", re.IGNORECASE),
                    ).first,
                    self.page.locator("button:has-text('Select Students')").first,
                    self.page.locator("a:has-text('Select Students')").first,
                ]
                trigger = None
                for t in triggers:
                    try:
                        if t.count() > 0 and t.is_visible():
                            trigger = t
                            break
                    except Exception:
                        pass
                if trigger is None:
                    return

                try:
                    trigger.click(timeout=12000)
                except Exception:
                    # Last resort for heavily scripted controls.
                    trigger.evaluate("(el) => el.click()")

                try:
                    self.page.locator(".modal.show, .modal.in").first.wait_for(
                        state="visible", timeout=15000
                    )
                    opened_modal = True
                except Exception:
                    # Some tenant flows redirect to login before student
                    # selection. Re-authenticate and continue.
                    current_url = self.page.url if self.page else ""
                    if "/login" in (current_url or ""):
                        logger.info(
                            "Select Students triggered login gate; re-authenticating."
                        )
                        if self._login_email and self._login_password:
                            self._login_impl(self._login_email, self._login_password)
                            dismiss_non_enrollment_modals()
                            self._wait_for_portal_idle(
                                timeout=self._class_detail_timeout_ms
                            )
                            post_login_url = self.page.url if self.page else ""
                            logger.info(
                                f"Post-login URL after Select Students gate: {post_login_url}"
                            )
                            if "/login" in (
                                post_login_url or ""
                            ) and "nextQueryParams=" in (post_login_url or ""):
                                try:
                                    qs = parse_qs(urlparse(post_login_url).query)
                                    next_raw = unquote(qs.get("next", [""])[0] or "")
                                    nqp_raw = qs.get("nextQueryParams", [""])[0]
                                    nqp = (
                                        json.loads(unquote(nqp_raw)) if nqp_raw else {}
                                    )
                                    next_path = str(
                                        nqp.get("next") or next_raw or ""
                                    ).lstrip("/")
                                    filters_payload = str(nqp.get("filters") or "")
                                    filters_obj = (
                                        json.loads(filters_payload)
                                        if filters_payload
                                        else {}
                                    )
                                    students_token = str(
                                        nqp.get("students")
                                        or filters_obj.get("students")
                                        or "7268"
                                    )
                                    if next_raw.startswith(
                                        "scaq/enroll/select-students"
                                    ):
                                        next_details = str(nqp.get("next") or "")
                                        select_students_url = (
                                            self.base_url.rstrip("/")
                                            + "/enroll/select-students"
                                            + f"?next={quote(next_details)}"
                                        )
                                        if nqp_raw:
                                            select_students_url += f"&nextQueryParams={quote(unquote(nqp_raw))}"
                                        logger.info(
                                            f"Following select-students next flow URL: {select_students_url}"
                                        )
                                        self._goto(
                                            select_students_url,
                                            description="select-students flow after login",
                                        )
                                        self._wait_for_portal_idle(
                                            timeout=self._class_detail_timeout_ms
                                        )
                                        for text in ("Continue", "Great!", "Close"):
                                            try:
                                                btn = self.page.get_by_role(
                                                    "button",
                                                    name=re.compile(
                                                        rf"^{re.escape(text)}$",
                                                        re.IGNORECASE,
                                                    ),
                                                ).first
                                                if btn.is_visible():
                                                    btn.click(timeout=7000)
                                                    self._wait_for_portal_idle(
                                                        timeout=self._class_detail_timeout_ms
                                                    )
                                            except Exception:
                                                pass
                                    elif next_path.startswith("class-details/"):
                                        target = (
                                            self.base_url.rstrip("/")
                                            + "/"
                                            + next_path
                                            + f"?selectedStudents={students_token}"
                                        )
                                        if filters_payload:
                                            target += (
                                                f"&filters={quote(filters_payload)}"
                                            )
                                        self._goto(
                                            target,
                                            description="class detail from login nextQueryParams",
                                        )
                                        self._wait_for_portal_idle(
                                            timeout=self._class_detail_timeout_ms
                                        )
                                        post_login_url = (
                                            self.page.url
                                            if self.page
                                            else post_login_url
                                        )
                                except Exception as e:
                                    logger.debug(
                                        f"Failed to recover class-detail URL from login nextQueryParams: {e}"
                                    )
                            # Prefer portal-native "next" flow first. Fall back
                            # to class-details if the app does not route there.
                            if (
                                detail_url_before_select
                                and "class-details" in detail_url_before_select
                                and "/enroll/select-students"
                                not in (post_login_url or "")
                                and "/class-details/" not in (post_login_url or "")
                            ):
                                self._goto(
                                    detail_url_before_select,
                                    description="class detail after login gate",
                                )
                            self._wait_for_class_detail_ready()
                            self._wait_for_portal_idle(
                                timeout=self._class_detail_timeout_ms
                            )
                        return
                    # Some pages swap content inline without opening bootstrap
                    # modal; continue with generic selection attempts.
                    self._write_debug_artifacts(
                        "select_students_modal_not_visible",
                        {
                            "class_index": class_index,
                            "page_url": self.page.url if self.page else "",
                        },
                    )
                    return
            except Exception:
                return

            # Try selecting at least one student in the modal.
            picked_student = False
            try:
                scope = ".modal.show, .modal.in" if opened_modal else "body"
                option = self.page.locator(
                    f"{scope} input[type='checkbox'], {scope} input[type='radio']"
                ).first
                if option.count() > 0:
                    option.check(timeout=5000)
                    picked_student = True
            except Exception:
                pass

            if not picked_student:
                # Fallback for card/list style student selectors with no direct
                # checkbox/radio controls.
                try:
                    picked_student = bool(
                        self.page.evaluate(
                            r"""() => {
                                const modal = document.querySelector('.modal.show, .modal.in');
                                if (!modal) return false;
                                const isVisible = (el) => {
                                    if (!el) return false;
                                    const style = window.getComputedStyle(el);
                                    return (
                                        style.display !== 'none' &&
                                        style.visibility !== 'hidden' &&
                                        (el.offsetWidth > 0 || el.offsetHeight > 0)
                                    );
                                };
                                const candidates = Array.from(
                                    modal.querySelectorAll(
                                        "[data-student-id], .card, .list-group-item, label, button"
                                    )
                                ).filter(isVisible);
                                for (const el of candidates) {
                                    const txt = (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
                                    if (!txt) continue;
                                    if (txt.includes('close') || txt.includes('cancel') || txt.includes('continue')) continue;
                                    el.click();
                                    return true;
                                }
                                return false;
                            }"""
                        )
                    )
                except Exception:
                    picked_student = False

            # Confirm selection.
            for action in ("Continue", "Apply", "Done", "Save", "Close"):
                try:
                    if opened_modal:
                        btn = self.page.locator(
                            f".modal.show button:has-text('{action}'), .modal.in button:has-text('{action}')"
                        ).first
                    else:
                        btn = self.page.locator(f"button:has-text('{action}')").first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(timeout=7000)
                        self._wait_for_portal_idle(timeout=15000)
                        return
                except Exception:
                    pass

            self._write_debug_artifacts(
                "select_students_confirm_missing",
                {
                    "class_index": class_index,
                    "page_url": self.page.url if self.page else "",
                    "picked_student": picked_student,
                },
            )

        def ensure_authenticated_detail_view() -> None:
            # Occasionally we land on class-details in a public/guest view
            # (Register/Log In links visible), which has no enroll controls.
            if not self._has_visible_guest_auth_links():
                return

            logger.info(
                "Detected guest class-details view; re-authenticating and reloading detail page."
            )
            if self._login_email and self._login_password:
                detail_url = self.page.url if self.page else ""
                self._login_impl(self._login_email, self._login_password)
                dismiss_non_enrollment_modals()
                if detail_url and "/class-details/" in detail_url:
                    self._goto(
                        detail_url, description="authenticated class detail view"
                    )
                    self._wait_for_class_detail_ready()
                    self._wait_for_portal_idle(timeout=self._class_detail_timeout_ms)

        # iClassPro flow is always: Enroll Now → Add to Cart (no shortcut path).
        dismiss_non_enrollment_modals()
        ensure_authenticated_detail_view()

        def _any_enroll_or_add_control_visible() -> bool:
            for loc in (
                self.page.get_by_role("button", name=add_to_cart_pattern).first,
                self.page.locator("button:has-text('Add to Cart')").first,
                self.page.get_by_role("button", name=enroll_pattern).first,
                self.page.locator("button:has-text('Enroll Now')").first,
            ):
                try:
                    if loc.is_visible():
                        return True
                except Exception:
                    pass
            return False

        if not _any_enroll_or_add_control_visible():
            ensure_student_selected()
            ensure_authenticated_detail_view()
            dismiss_non_enrollment_modals()

        def click_enroll_now():
            # Some portals require opening the "View Available Dates" modal
            # before any enroll/add controls are rendered.
            last_error = None
            for pass_idx in range(2):
                try:
                    view_dates = self.page.locator(
                        "a:has-text('View Available Dates')"
                    ).first
                    if view_dates.is_visible():
                        view_dates.click(timeout=20000)
                        self._wait_for_portal_idle(
                            timeout=self._class_detail_timeout_ms
                        )
                except Exception:
                    pass

                dismiss_non_enrollment_modals()
                candidates = [
                    self.page.get_by_role("button", name=enroll_pattern).first,
                    self.page.locator(
                        "customer-portal-class-details button:has-text('Enroll Now')"
                    ).first,
                    self.page.locator("button:has-text('Enroll Now')").first,
                    self.page.locator("a:has-text('Enroll Now')").first,
                    # Modal/session-specific controls
                    self.page.locator(
                        ".modal-view-dates button:has-text('Enroll')"
                    ).first,
                    self.page.locator(
                        ".modal-view-dates a:has-text('Enroll')"
                    ).first,
                    # Some flows require a modal "Continue" before rendering Add to Cart.
                    self.page.locator(
                        ".modal.show button:has-text('Continue')"
                    ).first,
                ]
                saw_candidate = False
                for ctl in candidates:
                    try:
                        if ctl.count() == 0:
                            continue
                        saw_candidate = True
                        ctl.scroll_into_view_if_needed(timeout=15000)
                        ctl.wait_for(state="visible", timeout=10000)
                        ctl.click(timeout=20000)
                        return
                    except Exception as e:
                        last_error = e

                if (
                    pass_idx == 0
                    and not saw_candidate
                    and "/class-details/" in (self.page.url or "")
                ):
                    logger.info(
                        "No enrollment controls rendered; reloading class-details once."
                    )
                    self.page.reload(wait_until="domcontentloaded", timeout=30000)
                    self._wait_for_portal_idle(
                        timeout=self._class_detail_timeout_ms
                    )
                    continue
            # Persist a focused artifact when enroll controls are missing.
            try:
                button_samples = self.page.locator("button").all_inner_texts()[:40]
            except Exception:
                button_samples = []
            try:
                link_samples = self.page.locator("a").all_inner_texts()[:60]
            except Exception:
                link_samples = []
            self._write_debug_artifacts(
                "enroll_control_missing",
                {
                    "class_index": class_index,
                    "page_url": self.page.url if self.page else "",
                    "buttons": button_samples,
                    "links": link_samples,
                    "error": str(last_error) if last_error else "unknown",
                },
            )
            raise last_error or RuntimeError("No clickable enroll control found.")

        logger.info("Step 1/2: Enroll Now.")
        _retry(
            click_enroll_now,
            action_name="Click enrollment control",
            attempts=3,
            base_delay=1.5,
            max_delay=6.0,
        )
        dismiss_non_enrollment_modals()
        self._wait_for_portal_idle(timeout=self._class_detail_timeout_ms)
        self._wait_for_post_enroll_before_add_to_cart(add_to_cart_pattern)

        initial_cart_count = self._get_cart_item_count()

        def click_add_to_cart():
            candidates = [
                self.page.get_by_role("button", name=add_to_cart_pattern).first,
                self.page.locator("button:has-text('Add to Cart')").first,
                self.page.locator("a:has-text('Add to Cart')").first,
            ]
            last_error = None
            for btn in candidates:
                try:
                    btn.scroll_into_view_if_needed(timeout=15000)
                    btn.click(timeout=20000)
                    return
                except Exception as e:
                    last_error = e
            # Persist a focused artifact when the enrollment controls are missing.
            self._write_debug_artifacts(
                "add_to_cart_control_missing",
                {
                    "class_index": class_index,
                    "page_url": self.page.url if self.page else "",
                    "error": str(last_error) if last_error else "unknown",
                },
            )
            raise last_error or RuntimeError("No clickable Add to Cart control found.")

        logger.info("Step 2/2: Add to Cart.")
        _retry(click_add_to_cart, action_name="Click 'Add to Cart'", attempts=3)
        self.page.wait_for_timeout(2500)

        enrollment_issue = self._get_enrollment_issue()
        if enrollment_issue:
            lowered = enrollment_issue.lower()
            if "already" in lowered:
                raise EnrollmentSkipped(enrollment_issue)
            raise RuntimeError(enrollment_issue)

        ok_confirm, final_cart_count, confirm_reason = (
            self._wait_for_cart_add_confirmation(
                initial_cart_count, timeout_ms=90000
            )
        )
        logger.info(
            f"Cart add confirmation ({confirm_reason}): initial={initial_cart_count}, "
            f"observed={final_cart_count}, ok={ok_confirm}"
        )

        if not ok_confirm:
            # Fallback: cart line items / badges often only render on /cart.
            try:
                cart_url = self.base_url.rstrip("/") + "/cart"
                self._goto(cart_url, description="cart verification")
                self._wait_for_portal_idle(timeout=15000)
                ok_confirm, final_cart_count, confirm_reason = (
                    self._wait_for_cart_add_confirmation(
                        initial_cart_count, timeout_ms=25000
                    )
                )
                logger.info(
                    f"Cart page re-check ({confirm_reason}): "
                    f"initial={initial_cart_count}, observed={final_cart_count}, ok={ok_confirm}"
                )
            except Exception as e:
                logger.debug(f"Cart-page verification fallback failed: {e}")

        if not ok_confirm:
            idempotency_state = self._detect_idempotency_state()
            if idempotency_state == "already_enrolled":
                raise EnrollmentSkipped("Class is already enrolled.")
            if idempotency_state == "already_in_cart":
                raise EnrollmentSkipped("Class is already in cart.")
            enrollment_issue = self._get_enrollment_issue()
            if enrollment_issue:
                lowered = enrollment_issue.lower()
                if "already" in lowered:
                    raise EnrollmentSkipped(enrollment_issue)
                raise RuntimeError(enrollment_issue)
            raise RuntimeError("Failed to verify that the class was added to the cart.")

        self.take_screenshot(f"after_add_to_cart_{class_index}.png", full_page=True)

    def _open_class_detail_page(
        self,
        location: str,
        timestr: str,
        daystr: str,
        student_id: int,
        class_index: int,
    ) -> None:
        """Navigate to the class search results and click through to the class detail page."""
        logger.info(f"Searching for class: {daystr} at {timestr} in {location}")
        day_index = DAY_TO_QUERY_INDEX.get(daystr.strip().lower())
        if day_index is None:
            valid_days = ", ".join(WEEK_DAYS)
            raise ValueError(f"Invalid day '{daystr}'. Expected one of: {valid_days}")

        day_query = f"&days={day_index}"
        student_query = f"&selectedStudents={student_id}"
        booking_url = f"{self.base_url}classes?q={location.replace(' ', '%20')}{day_query}{student_query}"

        self._goto(booking_url, description="class search results")
        self._wait_for_portal_idle(timeout=self._class_detail_timeout_ms)
        self.take_screenshot(f"classes_page_{class_index}.png", full_page=True)
        # For class-details URLs, use the configured student ID directly.
        # Some internal telemetry endpoints expose other numeric tokens, but
        # using those in selectedStudents can put the page into the wrong state.
        selected_student_token = str(student_id)

        # Find the link for the class time, waiting for it to appear
        class_link = self.page.locator(f"a:has-text('at {timestr}')")
        candidate_ids = []
        html_ids = []
        raw_ids = []
        observed_request_urls = []
        debug_snapshot = {}
        try:
            # Wait for at least one to be visible and actionable.
            class_link.first.wait_for(state="visible", timeout=20000)
            self._wait_for_portal_idle(timeout=self._class_detail_timeout_ms)

            # If there are multiple (e.g., this week and next week), pick the last one.
            # Where possible, resolve the underlying `/class-details/<id>` URL and open
            # it directly. That's more reliable than depending on SPA click behavior.
            count = class_link.count()
            logger.info(
                f"Found {count} class link(s) for {timestr}. Opening the latest matching details view."
            )

            # Primary no-click resolver: ask the open classes API for matching
            # classes and use classId directly. This avoids brittle SPA click
            # behavior entirely on portals where class cards use href="#".
            try:
                api_candidate_ids = (
                    self.page.evaluate(
                        r"""async ([locationName, timeStr, dayIdx]) => {
                        const norm = (v) => String(v || '').toLowerCase();
                        const orgSlug = (window.location.pathname.split('/').filter(Boolean)[0] || '').trim();
                        if (!orgSlug) return [];

                        const toEntries = (node, out = []) => {
                            if (Array.isArray(node)) {
                                for (const item of node) toEntries(item, out);
                                return out;
                            }
                            if (node && typeof node === 'object') {
                                out.push(node);
                                for (const key of Object.keys(node)) {
                                    toEntries(node[key], out);
                                }
                            }
                            return out;
                        };

                        const timeNeedle = norm(timeStr).replace(/\s+/g, '');
                        const locationNeedle = norm(locationName);

                        let locationId = null;
                        try {
                            const locResp = await fetch(`https://app.iclasspro.com/api/open/v1/${orgSlug}/locations`, { credentials: 'include' });
                            if (locResp.ok) {
                                const locJson = await locResp.json();
                                const locEntries = toEntries(locJson);
                                const best = locEntries.find((x) => {
                                    const name = norm(x?.name || x?.locationName || x?.title || '');
                                    return name && (name === locationNeedle || name.includes(locationNeedle));
                                });
                                if (best) {
                                    locationId = best.id || best.locationId || best.locationID || null;
                                }
                            }
                        } catch (e) {}

                        const params = new URLSearchParams();
                        params.set('q', locationName || '');
                        params.set('days', String(dayIdx || ''));
                        params.set('limit', '80');
                        params.set('page', '1');
                        if (locationId) params.set('locationId', String(locationId));

                        const clsResp = await fetch(
                            `https://app.iclasspro.com/api/open/v1/${orgSlug}/classes?${params.toString()}`,
                            { credentials: 'include' }
                        );
                        if (!clsResp.ok) return [];
                        const clsJson = await clsResp.json();
                        const entries = toEntries(clsJson);
                        const matches = [];
                        for (const entry of entries) {
                            const classId = entry?.classId || entry?.classID || entry?.id || null;
                            if (!classId) continue;
                            const text = norm(JSON.stringify(entry)).replace(/\s+/g, '');
                            if (!text.includes(locationNeedle.replace(/\s+/g, ''))) continue;
                            if (!text.includes(timeNeedle)) continue;
                            matches.push(String(classId));
                        }
                        return Array.from(new Set(matches));
                    }""",
                        [location, timestr, day_index],
                    )
                    or []
                )
                if api_candidate_ids:
                    logger.info(
                        f"Resolved {len(api_candidate_ids)} class ID candidate(s) from open classes API."
                    )
                    candidate_ids.extend(api_candidate_ids)
            except Exception as api_error:
                logger.debug(
                    f"Open classes API class-id resolution unavailable: {api_error}"
                )

            # Portal-specific fast path: card anchors are often href="#", and the
            # true class resolution happens via "View Available Dates" modal/API.
            # Try harvesting class IDs from the sessions API triggered by that link.
            try:
                sessions_candidate_ids = []

                def _collect_ids_from_payload(node):
                    out = []
                    if isinstance(node, dict):
                        payload_text = json.dumps(node).lower()
                        class_id = (
                            node.get("classId")
                            or node.get("classID")
                            or node.get("class_id")
                        )
                        if class_id and str(class_id).isdigit():
                            out.append((str(class_id), payload_text))
                        for value in node.values():
                            out.extend(_collect_ids_from_payload(value))
                    elif isinstance(node, list):
                        for item in node:
                            out.extend(_collect_ids_from_payload(item))
                    return out

                with self.page.expect_response(
                    re.compile(r"/api/open/v1/.*/sessions"), timeout=12000
                ) as sessions_resp:
                    class_link.last.evaluate(
                        r"""el => {
                            const card = el.closest('article') || el;
                            const trigger = card.querySelector("a[data-target*='modal-view-dates'], a.text-link");
                            if (trigger) trigger.click();
                            else el.click();
                        }"""
                    )
                payload = sessions_resp.value.json()
                for class_id, payload_text in _collect_ids_from_payload(payload):
                    if (
                        timestr.strip().lower() in payload_text
                        and location.strip().lower() in payload_text
                    ):
                        sessions_candidate_ids.append(class_id)
                sessions_candidate_ids = list(dict.fromkeys(sessions_candidate_ids))
                if sessions_candidate_ids:
                    logger.info(
                        f"Resolved {len(sessions_candidate_ids)} class ID candidate(s) from sessions API."
                    )
                    candidate_ids.extend(sessions_candidate_ids)
            except Exception as sessions_error:
                logger.debug(
                    f"Sessions API class-id resolution unavailable: {sessions_error}"
                )

            detail_href = class_link.last.evaluate(
                r"""el => {
                    const directHref = el.getAttribute('href') || '';
                    if (/\/class-details\/\d+/.test(directHref)) return directHref;

                    let node = el.parentElement;
                    while (node && node.tagName !== 'BODY') {
                        const links = node.querySelectorAll("a[href*='class-details']");
                        for (const lnk of links) {
                            const href = lnk.getAttribute('href') || '';
                            if (/\/class-details\/\d+/.test(href)) return href;
                        }
                        node = node.parentElement;
                    }
                    return directHref;
                }"""
            )

            class_id_match = re.search(r"/class-details/(\d+)", detail_href or "")

            if not class_id_match:
                # Broader scan: walk every class-details anchor on the page and
                # match by nearby text (the time string). This handles cases
                # where the time-bearing card anchor is not a structural
                # ancestor of the actual class-details link.
                global_href = self.page.evaluate(
                    r"""([timeStr]) => {
                        const target = String(timeStr || '').toLowerCase();
                        if (!target) return '';
                        const anchors = Array.from(
                            document.querySelectorAll("a[href*='class-details']")
                        );
                        for (const a of anchors) {
                            const href = a.getAttribute('href') || '';
                            if (!/\/class-details\/\d+/.test(href)) continue;
                            let node = a;
                            for (let depth = 0; depth < 6 && node; depth++) {
                                const text = (node.textContent || '').toLowerCase();
                                if (text.includes('at ' + target)) {
                                    return href;
                                }
                                node = node.parentElement;
                            }
                        }
                        return '';
                    }""",
                    [timestr],
                )
                class_id_match = re.search(r"/class-details/(\d+)", global_href or "")
                if class_id_match:
                    logger.info("Resolved class detail URL via global DOM scan.")

            if class_id_match:
                filters_json = json.dumps(
                    {
                        "q": location,
                        "students": str(selected_student_token),
                        "days": str(day_index),
                    }
                )
                class_url = (
                    f"{self.base_url.rstrip('/')}/class-details/{class_id_match.group(1)}"
                    f"?selectedStudents={selected_student_token}&filters={quote(filters_json)}"
                )
                logger.info(f"Opening class detail URL directly: {class_url}")
                self._goto(class_url, description="class detail page")
                self._wait_for_class_detail_ready()
                return

            last_error = None

            # Last non-click fallback: try candidate class-details links found on
            # the filtered results page. Some portal variants render links that
            # are not DOM-near the time anchor and don't respond to synthetic
            # click/navigation events.
            dom_candidate_ids = (
                self.page.evaluate(
                    r"""([timeStr]) => {
                    const target = String(timeStr || '').toLowerCase();
                    const anchors = Array.from(
                        document.querySelectorAll("a[href*='class-details']")
                    );
                    const ranked = [];
                    for (const a of anchors) {
                        const href = a.getAttribute('href') || '';
                        const m = href.match(/\/class-details\/(\d+)/);
                        if (!m) continue;
                        const id = m[1];
                        let score = 0;
                        const ownText = (a.textContent || '').toLowerCase();
                        if (target && ownText.includes('at ' + target)) score = 2;
                        let node = a.parentElement;
                        for (let depth = 0; depth < 6 && node; depth++) {
                            const text = (node.textContent || '').toLowerCase();
                            if (target && text.includes('at ' + target)) {
                                score = Math.max(score, 3);
                                break;
                            }
                            node = node.parentElement;
                        }
                        ranked.push({ id, score });
                    }
                    ranked.sort((a, b) => b.score - a.score);
                    const seen = new Set();
                    const ordered = [];
                    for (const item of ranked) {
                        if (!seen.has(item.id)) {
                            seen.add(item.id);
                            ordered.push(item.id);
                        }
                    }
                    return ordered;
                }""",
                    [timestr],
                )
                or []
            )
            candidate_ids = list(dict.fromkeys(candidate_ids + dom_candidate_ids))
            if not candidate_ids:
                # Some pages hide the actual class-details links behind a
                # "View Available Dates" action. Expand those cards first, then
                # re-scan for class-details IDs.
                self.page.evaluate(
                    r"""([timeStr]) => {
                        const target = String(timeStr || '').toLowerCase();
                        const allAnchors = Array.from(document.querySelectorAll("a"));
                        for (const a of allAnchors) {
                            const txt = (a.textContent || '').toLowerCase().trim();
                            if (!txt.includes('view available dates')) continue;
                            let node = a.parentElement;
                            let nearTarget = false;
                            for (let depth = 0; depth < 6 && node; depth++) {
                                const blockText = (node.textContent || '').toLowerCase();
                                if (target && blockText.includes('at ' + target)) {
                                    nearTarget = true;
                                    break;
                                }
                                node = node.parentElement;
                            }
                            if (nearTarget) {
                                try { a.click(); } catch (e) {}
                            }
                        }
                    }""",
                    [timestr],
                )
                self.page.wait_for_timeout(800)
                dom_candidate_ids = (
                    self.page.evaluate(
                        r"""([timeStr]) => {
                        const target = String(timeStr || '').toLowerCase();
                        const anchors = Array.from(
                            document.querySelectorAll("a[href*='class-details']")
                        );
                        const ranked = [];
                        for (const a of anchors) {
                            const href = a.getAttribute('href') || '';
                            const m = href.match(/\/class-details\/(\d+)/);
                            if (!m) continue;
                            const id = m[1];
                            let score = 0;
                            const ownText = (a.textContent || '').toLowerCase();
                            if (target && ownText.includes('at ' + target)) score = 2;
                            let node = a.parentElement;
                            for (let depth = 0; depth < 6 && node; depth++) {
                                const text = (node.textContent || '').toLowerCase();
                                if (target && text.includes('at ' + target)) {
                                    score = Math.max(score, 3);
                                    break;
                                }
                                node = node.parentElement;
                            }
                            ranked.push({ id, score });
                        }
                        ranked.sort((a, b) => b.score - a.score);
                        const seen = new Set();
                        const ordered = [];
                        for (const item of ranked) {
                            if (!seen.has(item.id)) {
                                seen.add(item.id);
                                ordered.push(item.id);
                            }
                        }
                        return ordered;
                    }""",
                        [timestr],
                    )
                    or []
                )
                candidate_ids = list(dict.fromkeys(candidate_ids + dom_candidate_ids))
                if candidate_ids:
                    logger.info(
                        "Resolved class-details candidates after expanding 'View Available Dates'."
                    )
            if candidate_ids:
                filters_json = json.dumps(
                    {
                        "q": location,
                        "students": str(selected_student_token),
                        "days": str(day_index),
                    }
                )
                logger.info(
                    f"Trying up to {min(len(candidate_ids), 6)} candidate class-details URL(s) resolved from page anchors."
                )
                for class_id in candidate_ids[:6]:
                    class_url = (
                        f"{self.base_url.rstrip('/')}/class-details/{class_id}"
                        f"?selectedStudents={selected_student_token}&filters={quote(filters_json)}"
                    )
                    try:
                        self._goto(class_url, description="class detail page")
                        self._wait_for_class_detail_ready(
                            timeout=self._class_detail_timeout_ms
                        )
                        logger.info(
                            f"Opened class detail directly from candidate link: {class_url}"
                        )
                        return
                    except Exception as candidate_error:
                        last_error = candidate_error
                        logger.debug(
                            f"Candidate class-details URL did not open cleanly ({class_url}): {candidate_error}"
                        )
                        self._goto(booking_url, description="class search results")
                        self._wait_for_portal_idle(
                            timeout=self._class_detail_timeout_ms
                        )
                        class_link = self.page.locator(f"a:has-text('at {timestr}')")
                        class_link.first.wait_for(state="visible", timeout=30000)

            if not candidate_ids:
                # Last URL-based recovery: scan the full rendered HTML (including
                # embedded scripts/state blobs) for class-details IDs, then try
                # opening a few of them directly.
                html_ids = (
                    self.page.evaluate(
                        r"""() => {
                        const html = document.documentElement?.outerHTML || '';
                        const matches = html.match(/\/class-details\/(\d+)/g) || [];
                        const seen = new Set();
                        const ids = [];
                        for (const m of matches) {
                            const idMatch = m.match(/(\d+)/);
                            if (!idMatch) continue;
                            const id = idMatch[1];
                            if (!seen.has(id)) {
                                seen.add(id);
                                ids.push(id);
                            }
                        }
                        return ids;
                    }"""
                    )
                    or []
                )
                if html_ids:
                    filters_json = json.dumps(
                        {
                            "q": location,
                            "students": str(selected_student_token),
                            "days": str(day_index),
                        }
                    )
                    logger.info(
                        f"Trying up to {min(len(html_ids), 8)} class-details URL(s) discovered from page HTML."
                    )
                    for class_id in html_ids[:8]:
                        class_url = (
                            f"{self.base_url.rstrip('/')}/class-details/{class_id}"
                            f"?selectedStudents={selected_student_token}&filters={quote(filters_json)}"
                        )
                        try:
                            self._goto(class_url, description="class detail page")
                            self._wait_for_class_detail_ready(
                                timeout=self._class_detail_timeout_ms
                            )
                            logger.info(
                                f"Opened class detail directly from page HTML match: {class_url}"
                            )
                            return
                        except Exception as html_candidate_error:
                            last_error = html_candidate_error
                            logger.debug(
                                f"HTML-derived class-details URL did not open cleanly ({class_url}): {html_candidate_error}"
                            )
                            self._goto(booking_url, description="class search results")
                            self._wait_for_portal_idle(
                                timeout=self._class_detail_timeout_ms
                            )
                            class_link = self.page.locator(
                                f"a:has-text('at {timestr}')"
                            )
                            class_link.first.wait_for(state="visible", timeout=30000)

            if not candidate_ids:
                # Some portal builds expose only raw class IDs (e.g. classId)
                # inside script state. Extract and probe those IDs directly.
                raw_ids = (
                    self.page.evaluate(
                        r"""() => {
                        const html = document.documentElement?.outerHTML || '';
                        const patterns = [
                            /"classId"\s*:\s*(\d+)/g,
                            /"classID"\s*:\s*(\d+)/g,
                            /data-class-id=["'](\d+)["']/g,
                            /classId[=:]\s*["']?(\d+)["']?/g
                        ];
                        const seen = new Set();
                        const ids = [];
                        for (const re of patterns) {
                            re.lastIndex = 0;
                            let m;
                            while ((m = re.exec(html)) !== null) {
                                const id = m[1];
                                if (!seen.has(id)) {
                                    seen.add(id);
                                    ids.push(id);
                                }
                            }
                        }
                        return ids;
                    }"""
                    )
                    or []
                )
                if raw_ids:
                    filters_json = json.dumps(
                        {
                            "q": location,
                            "students": str(selected_student_token),
                            "days": str(day_index),
                        }
                    )
                    target_tokens = [
                        daystr.strip().lower(),
                        timestr.strip().lower(),
                        location.strip().lower(),
                    ]
                    logger.info(
                        f"Trying up to {min(len(raw_ids), 20)} class-details URL(s) derived from raw class IDs."
                    )
                    for class_id in raw_ids[:20]:
                        class_url = (
                            f"{self.base_url.rstrip('/')}/class-details/{class_id}"
                            f"?selectedStudents={selected_student_token}&filters={quote(filters_json)}"
                        )
                        try:
                            self._goto(class_url, description="class detail page")
                            self._wait_for_class_detail_ready(
                                timeout=self._class_detail_timeout_ms
                            )
                            body_text = (
                                self.page.locator("body")
                                .inner_text(timeout=3000)
                                .lower()
                            )
                            # Prevent enrolling the wrong class when probing.
                            if all(token in body_text for token in target_tokens):
                                logger.info(
                                    f"Opened matching class detail from raw class ID: {class_url}"
                                )
                                return
                        except Exception as raw_id_error:
                            last_error = raw_id_error
                        self._goto(booking_url, description="class search results")
                        self._wait_for_portal_idle(
                            timeout=self._class_detail_timeout_ms
                        )
                        class_link = self.page.locator(f"a:has-text('at {timestr}')")
                        class_link.first.wait_for(state="visible", timeout=30000)

            for attempt_label, use_force, use_js in (
                ("standard click", False, False),
                ("forced click", True, False),
                ("javascript click", False, True),
            ):
                _capture_request = None
                try:
                    logger.info(
                        f"Opening class detail via {attempt_label} for {daystr} at {timestr}."
                    )
                    class_link.last.scroll_into_view_if_needed(timeout=10000)
                    captured_urls = []

                    def _capture_request(req):
                        try:
                            req_url = req.url or ""
                            if "/class-details/" in req_url:
                                captured_urls.append(req_url)
                            observed_request_urls.append(req_url)
                        except Exception:
                            pass

                    self.page.on("request", _capture_request)
                    if use_js:
                        class_link.last.evaluate("(el) => el.click()")
                    else:
                        class_link.last.click(timeout=15000, force=use_force)
                    try:
                        self._wait_for_class_detail_ready(
                            timeout=self._class_detail_timeout_ms
                        )
                        return
                    except Exception as click_wait_error:
                        # Some portal variants don't navigate, but they do fire
                        # a request containing the class-details URL.
                        captured_id = None
                        for req_url in captured_urls:
                            m = re.search(r"/class-details/(\d+)", req_url)
                            if m:
                                captured_id = m.group(1)
                                break
                        if captured_id:
                            filters_json = json.dumps(
                                {
                                    "q": location,
                                    "students": str(selected_student_token),
                                    "days": str(day_index),
                                }
                            )
                            captured_url = (
                                f"{self.base_url.rstrip('/')}/class-details/{captured_id}"
                                f"?selectedStudents={selected_student_token}&filters={quote(filters_json)}"
                            )
                            logger.info(
                                "Recovered class details URL from click-triggered network request."
                            )
                            self._goto(captured_url, description="class detail page")
                            self._wait_for_class_detail_ready(
                                timeout=self._class_detail_timeout_ms
                            )
                            return
                        raise click_wait_error
                except Exception as attempt_error:
                    last_error = attempt_error
                    logger.warning(
                        f"Class detail did not open via {attempt_label}; retrying if possible."
                    )
                    self._goto(booking_url, description="class search results")
                    self._wait_for_portal_idle(timeout=self._class_detail_timeout_ms)
                    class_link = self.page.locator(f"a:has-text('at {timestr}')")
                    class_link.first.wait_for(state="visible", timeout=30000)
                finally:
                    if _capture_request is not None:
                        try:
                            self.page.remove_listener("request", _capture_request)
                        except Exception:
                            pass

            # Capture focused diagnostics to make portal-specific selector
            # mismatches visible in logs.
            try:
                debug_info = self.page.evaluate(
                    r"""([timeStr]) => {
                        const target = String(timeStr || '').toLowerCase();
                        const anchors = Array.from(document.querySelectorAll("a"));
                        const classDetailAnchors = anchors.filter(a => {
                            const h = a.getAttribute('href') || '';
                            return /\/class-details\/\d+/.test(h);
                        });
                        const viewDatesAnchors = anchors.filter(a => {
                            const t = (a.textContent || '').toLowerCase();
                            return t.includes('view available dates');
                        });
                        const timeAnchors = anchors
                            .filter(a => {
                                const t = (a.textContent || '').toLowerCase();
                                return target && t.includes('at ' + target);
                            })
                            .slice(0, 5)
                            .map(a => {
                                const txt = (a.textContent || '').replace(/\s+/g, ' ').trim();
                                const href = a.getAttribute('href') || '';
                                return { text: txt.slice(0, 140), href };
                            });
                        return {
                            class_details_anchor_count: classDetailAnchors.length,
                            view_available_dates_count: viewDatesAnchors.length,
                            time_anchor_samples: timeAnchors
                        };
                    }""",
                    [timestr],
                )
                debug_snapshot = debug_info or {}
                logger.warning(
                    f"Class detail debug snapshot for {daystr} {timestr} {location}: {json.dumps(debug_snapshot)}"
                )
            except Exception as debug_error:
                logger.debug(
                    f"Failed to capture class detail debug snapshot: {debug_error}"
                )

            raise last_error or RuntimeError("Class detail page did not open.")
        except Exception as error:
            self._write_debug_artifacts(
                "class_detail_open_failure",
                {
                    "day": daystr,
                    "time": timestr,
                    "location": location,
                    "booking_url": booking_url,
                    "page_url": self.page.url if self.page else "",
                    "candidate_ids": candidate_ids,
                    "html_ids": html_ids,
                    "raw_ids": raw_ids,
                    "observed_request_urls": observed_request_urls[-200:],
                    "debug_snapshot": debug_snapshot,
                    "error": str(error),
                },
            )
            raise RuntimeError(
                f"Could not open class details for {daystr} at {timestr} in {location}: {error}"
            ) from error

    def _extract_detail_field(self, label: str) -> str:
        """Extract a labeled field value from the class detail page."""
        script = """
        ([labelText]) => {
            const normalized = (value) => (value || '').replace(/\s+/g, ' ').trim();
            const labelPrefix = `${labelText.toLowerCase()}:`;
            const readFollowingText = (elements) => {
                for (const element of elements) {
                    const text = normalized(element?.textContent);
                    if (text) {
                        return text;
                    }
                }
                return '';
            };

            const elements = Array.from(document.querySelectorAll('body *'));
            for (const el of elements) {
                const elementText = normalized(el.textContent);
                const elementTextLower = elementText.toLowerCase();
                if (!elementTextLower.startsWith(labelPrefix)) {
                    continue;
                }

                const inlineValue = elementText.slice(labelText.length + 1).trim();
                if (inlineValue) {
                    return inlineValue;
                }

                let sibling = el.nextElementSibling;
                while (sibling) {
                    const text = normalized(sibling.textContent);
                    if (text) {
                        return text;
                    }
                    sibling = sibling.nextElementSibling;
                }

                const parent = el.parentElement;
                if (!parent) {
                    continue;
                }

                const children = Array.from(parent.children);
                const index = children.indexOf(el);
                for (let i = index + 1; i < children.length; i += 1) {
                    const text = normalized(children[i].textContent);
                    if (text) {
                        return text;
                    }
                }

                let container = parent;
                while (container) {
                    const containerChildren = Array.from(container.children);
                    const containerIndex = containerChildren.indexOf(parent);
                    if (containerIndex >= 0) {
                        const siblingText = readFollowingText(
                            containerChildren.slice(containerIndex + 1)
                        );
                        if (siblingText) {
                            return siblingText;
                        }
                    }

                    const row = container.closest('.row, [class*="row"]');
                    if (row) {
                        const rowChildren = Array.from(row.children);
                        const rowIndex = rowChildren.indexOf(container);
                        if (rowIndex >= 0) {
                            const rowText = readFollowingText(
                                rowChildren.slice(rowIndex + 1)
                            );
                            if (rowText) {
                                return rowText;
                            }
                        }
                    }

                    container = container.parentElement;
                }

                const parentText = normalized(parent.textContent);
                if (parentText.toLowerCase().startsWith(labelPrefix)) {
                    const inlineParentValue = parentText.slice(labelText.length + 1).trim();
                    if (inlineParentValue) {
                        return inlineParentValue;
                    }
                }
            }

            return '';
        }
        """
        try:
            self.page.locator(f"text=/^{label}:/i").first.wait_for(
                state="attached", timeout=5000
            )
            return (self.page.evaluate(script, [label]) or "").strip()
        except Exception as error:
            logger.debug(f"Could not extract detail field '{label}': {error}")
            return ""

    def process_cart(
        self, promo_code: str = "", complete_transaction: bool = False
    ) -> None:
        """Navigates to the cart and completes checkout."""
        logger.info("Processing cart...")
        cart_url = self.base_url.rstrip("/") + "/cart"
        self._goto(cart_url, description="cart page")
        self.page.wait_for_timeout(5000)  # Extra wait for cart to render fully

        self.take_screenshot("cart_final.png", full_page=True)

        if self._get_cart_item_count() == 0:
            logger.warning("Cart is empty. Nothing to process.")
            return

        if promo_code:
            logger.info(f"Applying promo code: {promo_code}")
            self.page.locator("a:has-text('Use Promo Code')").click()
            self.page.wait_for_timeout(1000)
            self.page.locator("[name='promoCode']").fill(promo_code)
            self.page.locator("button:has-text('Apply')").click()
            self.page.wait_for_timeout(2000)

        if complete_transaction:
            logger.info("Attempting to complete transaction...")

            def click_complete():
                self.page.locator("button:has-text('Complete Transaction')").click(
                    timeout=15000
                )

            _retry(
                click_complete, action_name="Click 'Complete Transaction'", attempts=3
            )
            logger.info("Waiting 15 seconds for transaction to finalize...")
            self.page.wait_for_timeout(15000)
            self.take_screenshot("transaction_complete.png", full_page=True)
            logger.info("Transaction submitted.")
        else:
            logger.info("Dry run enabled. Skipping final transaction completion.")

        logger.info("Cart processing complete.")

    def enroll_by_url(self, url: str, class_index: int) -> None:
        """Navigate directly to a class URL and add it to the cart."""
        logger.info(f"Enrolling via direct URL: {url}")
        self._goto(url, description="class detail page")
        self.take_screenshot(f"classes_page_url_{class_index}.png", full_page=True)
        self._wait_for_class_detail_ready()
        self._add_current_class_to_cart(class_index=class_index)

    def close(self):
        """Safely close the browser and Playwright instances."""
        self._is_shutting_down = True
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                self.browser.close()
                logger.info("Browser closed.")
            except Exception:
                pass
        if self.playwright:
            try:
                self.playwright.stop()
                logger.info("Playwright stopped.")
            except Exception:
                pass

        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None
        self._is_shutting_down = False


# =============================================================================
# Open API — class discovery (public catalog; no authentication)
# =============================================================================

OPEN_API_BASE = "https://app.iclasspro.com/api/open/v1"

_PLAIN_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(am|pm)", re.IGNORECASE)
_OPEN_PLAIN_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*([AP]M)$", re.IGNORECASE)


def _default_portal_slug() -> str:
    """Org slug (first path segment of the portal URL), e.g. ``scaq`` for ``/scaq/classes``."""
    env_portal = (os.getenv("ICLASS_PORTAL") or "").strip()
    if env_portal:
        return env_portal
    base = (os.getenv("ICLASS_BASE_URL") or "").strip()
    if base:
        try:
            path = urlparse(base).path.strip("/")
            if path:
                return path.split("/")[0]
        except Exception:
            pass
    return "scaq"


# ---------------------------------------------------------------------------
# Time helpers (Open API schedule strings)
# ---------------------------------------------------------------------------
def _http_normalize_schedule_time(raw: str) -> str:
    """Normalise a time string to canonical 'H:MMam' form."""
    raw = raw.strip().lower().replace(" ", "")
    m = _PLAIN_TIME_RE.match(raw)
    if m:
        h, mn, period = int(m.group(1)), m.group(2), m.group(3).lower()
        return f"{h}:{mn}{period}"
    if ":" in raw and len(raw) == 5:
        h_str, mn = raw.split(":")
        h = int(h_str)
        period = "am" if h < 12 else "pm"
        if h == 0:
            h = 12
        elif h > 12:
            h -= 12
        return f"{h}:{mn}{period}"
    return raw


def _normalize_open_time(raw: str) -> str:
    """Convert Open API times like '10:30AM' to schedule-friendly '10:30am'."""
    raw = (raw or "").strip()
    m = _OPEN_PLAIN_TIME_RE.match(raw.replace(" ", ""))
    if m:
        h, mn, ap = int(m.group(1)), m.group(2), m.group(3).lower()
        return f"{h}:{mn}{ap}"
    return _http_normalize_schedule_time(raw)


# ---------------------------------------------------------------------------
# Open API (no authentication — used for class discovery / scrape)
# ---------------------------------------------------------------------------
def _open_api_fetch_classes_all(org_slug: str) -> list:
    """Paginate GET ``/api/open/v1/{slug}/classes``."""
    slug = org_slug.strip().strip("/")
    url = f"{OPEN_API_BASE}/{slug}/classes"
    out: list = []
    page = 1
    limit = 80
    while True:
        resp = requests.get(url, params={"limit": limit, "page": page}, timeout=45)
        resp.raise_for_status()
        body = resp.json()
        rows = body.get("data") or []
        out.extend(rows)
        total = int(body.get("totalRecords") or 0)
        if not rows or page * limit >= total:
            break
        page += 1
    return out


def _open_row_to_discovery(row: dict, portal_slug: str, student_id: int) -> dict:
    """Map one Open API class row to the shape emitted by iclasspro.py scrape + UI."""
    name = (row.get("name") or "").strip()
    sched_list = row.get("schedule") or []
    sched = sched_list[0] if sched_list else {}
    day_num = int(sched.get("dayNumber") or 0)
    day_name = WEEK_DAYS[day_num - 1] if 1 <= day_num <= 7 else ""
    time_str = _normalize_open_time(str(sched.get("startTime") or ""))

    location = ""
    colon = name.find(":")
    if colon > 0:
        location = name[:colon].strip()

    instructors = row.get("instructors") or []
    instructor = str(instructors[0]).strip() if instructors else ""

    cid = row.get("id")
    filters_json = json.dumps({"students": str(student_id), "days": str(day_num)})
    base = f"https://portal.iclasspro.com/{portal_slug.strip('/').strip()}/"
    class_url = ""
    if cid is not None:
        class_url = (
            f"{base.rstrip('/')}/class-details/{cid}"
            f"?selectedStudents={student_id}&filters={quote(filters_json)}"
        )

    return {
        "name": name,
        "Location": location,
        "Day": day_name,
        "Time": time_str,
        "url": class_url,
        "Instructor": instructor,
    }


def scrape_classes_open(
    portal_slug: str,
    student_id: int,
    days: Optional[list] = None,
    locations: Optional[list] = None,
) -> list:
    """Discover classes via the public Open API (no JWT login required)."""
    days_norm = [d.strip().lower() for d in (days or []) if d.strip()]
    locs_norm = [l.strip().lower() for l in (locations or []) if l.strip()]

    logger.info("Fetching class catalog from Open API (org=%s)...", portal_slug)
    rows = _open_api_fetch_classes_all(portal_slug)
    seen: set[Any] = set()
    results = []

    for row in rows:
        cid = row.get("id")
        if not (row.get("schedule") or []):
            logger.debug("Skipping class id=%s (no schedule)", cid)
            continue
        entry = _open_row_to_discovery(row, portal_slug, student_id)

        if days_norm and entry["Day"].lower() not in days_norm:
            continue
        if locs_norm:
            loc_l = entry["Location"].lower()
            if not any(lf in loc_l for lf in locs_norm):
                continue

        if cid in seen:
            continue
        seen.add(cid)
        results.append(entry)
        print(
            f"  [{entry['Day']}] {entry['Location']} at {entry['Time']}"
            f"  (id={cid}, instructor={entry['Instructor']})"
        )

    return results


def _setup_logging_http(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def open_api_discovery_cli(args: argparse.Namespace) -> int:
    """Discover classes via the public Open API (``--scrape``)."""
    _setup_logging_http(logging.DEBUG if args.deep_debug else logging.INFO)

    if not args.student_id:
        logger.error("--student-id required (or ICLASS_STUDENT_ID env var).")
        return 1

    portal_slug = args.portal or _default_portal_slug()

    days = (
        [d.strip() for d in args.scrape_days.split(",")]
        if args.scrape_days
        else None
    )
    locations = (
        [l.strip() for l in args.scrape_locations.split(",")]
        if args.scrape_locations
        else None
    )
    print("\n=== Available Classes ===")
    try:
        found = scrape_classes_open(
            portal_slug, args.student_id, days=days, locations=locations
        )
    except requests.HTTPError as exc:
        logger.error("Open API request failed: %s", exc)
        if exc.response is not None:
            logger.error("Response: %s", exc.response.text[:500])
        return 1
    print(f"CLASSES_JSON:{json.dumps(found)}", flush=True)
    print(f"\nFound {len(found)} class(es) matching filters.")
    return 0


def _default_cli_driver() -> str:
    """Enrollment driver from env (``api`` default)."""
    enroll = (os.getenv("ICLASS_ENROLLMENT_DRIVER") or "").strip().lower()
    if enroll == "playwright":
        return "playwright"
    if enroll == "api":
        return "api"
    legacy = (os.getenv("ICLASS_DRIVER") or "").strip().lower()
    if legacy == "api" and os.getenv("ICLASS_ENROLLMENT_API", "0").lower() in (
        "1",
        "true",
        "yes",
    ):
        return "api"
    return "api"


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="iClassPro Enrollment Bot")
    parser.add_argument(
        "--driver",
        type=str,
        choices=["playwright", "api"],
        default=_default_cli_driver(),
        help=(
            "api: HTTP JWT enrollment (default, fast). "
            "playwright: browser automation (fallback if JWT login fails)."
        ),
    )
    parser.add_argument(
        "--portal",
        type=str,
        default=os.getenv("ICLASS_PORTAL"),
        help="Org slug for Open API discovery (--scrape); optional if ICLASS_BASE_URL is set.",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("ICLASS_BASE_URL", "https://portal.iclasspro.com/scaq/"),
        help="Portal base URL",
    )
    parser.add_argument(
        "--scrape",
        action="store_true",
        default=False,
        help="Discover classes via the public Open API and print JSON (no browser).",
    )
    parser.add_argument(
        "--scrape-days",
        type=str,
        default="",
        help=(
            "Comma-separated list of days to include during scrape "
            "(e.g. 'Sunday,Monday').  Empty means all days."
        ),
    )
    parser.add_argument(
        "--scrape-locations",
        type=str,
        default="",
        help=(
            "Comma-separated list of locations to include during scrape "
            "(e.g. 'Culver,El Segundo').  Empty means all locations."
        ),
    )
    parser.add_argument(
        "--schedule",
        type=str,
        default=os.getenv("ICLASS_SCHEDULE", "schedules/schedule.json"),
        help="Path to schedule JSON file",
    )
    parser.add_argument(
        "--email", type=str, default=os.getenv("ICLASS_EMAIL"), help="Login email"
    )
    parser.add_argument(
        "--password",
        type=str,
        default=os.getenv("ICLASS_PASSWORD"),
        help="Login password",
    )
    parser.add_argument(
        "--student-id",
        type=int,
        default=os.getenv("ICLASS_STUDENT_ID"),
        help="Student ID",
    )
    parser.add_argument(
        "--promo-code",
        type=str,
        default=os.getenv("ICLASS_PROMO_CODE", ""),
        help="Promo code",
    )
    parser.add_argument(
        "--complete-transaction",
        action="store_true",
        default=os.getenv("ICLASS_COMPLETE_TRANSACTION", "0").lower()
        in ("1", "true", "yes"),
        help="If set, actually complete the transaction by clicking the 'Complete Transaction' button.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Force dry-run mode (never click 'Complete Transaction').",
    )
    parser.add_argument(
        "--save-screenshots",
        action="store_true",
        default=os.getenv("ICLASS_SAVE_SCREENSHOTS", "0").lower()
        in ("1", "true", "yes"),
        help="If set, save screenshots during the process.",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        default=os.getenv("ICLASS_SEND_EMAIL", "0").lower() in ("1", "true", "yes"),
        help="If set, send the log file via email after completion.",
    )
    parser.add_argument(
        "--deep-debug",
        action="store_true",
        default=os.getenv("ICLASS_DEEP_DEBUG", "0").lower() in ("1", "true", "yes"),
        help="If set, write deep debug artifacts on interaction failures.",
    )
    parser.add_argument(
        "--report-path",
        type=str,
        default=os.getenv("ICLASS_RUN_REPORT", "run_report.json"),
        help="Path to write the structured run report JSON.",
    )
    parser.add_argument(
        "--storage-state",
        type=str,
        default=os.getenv("ICLASS_STORAGE_STATE", "storage_state.json"),
        help="Path to persist Playwright session state across runs.",
    )
    args = parser.parse_args()
    if args.scrape:
        sys.exit(open_api_discovery_cli(args))

    if args.driver == "api":
        from iclasspro_jwt import run_api_enrollment

        sys.exit(run_api_enrollment(args))

    # --- Setup Logging ---
    log_file = "iclasspro.log"
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Create a formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    # Create a file handler
    file_handler = logging.FileHandler(log_file, mode="w")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    # Create a stream handler (for console output)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    if not all([args.email, args.password, args.student_id]):
        logger.error(
            "Missing required environment variables or arguments: ICLASS_EMAIL, ICLASS_PASSWORD, ICLASS_STUDENT_ID"
        )
        exit(1)

    logger.info(f"Starting iClassPro enrollment bot for email: {args.email}")
    logger.info(f"Password: {'*' * len(args.password) if args.password else 'Not set'}")
    logger.info(f"Student ID: {args.student_id}")
    logger.info(f"Deep debug enabled: {bool(args.deep_debug)}")
    logger.info(f"Send email enabled (effective): {bool(args.send_email)}")
    logger.info(
        "Class detail readiness timeout (ms): "
        f"{os.getenv('ICLASS_CLASS_DETAIL_TIMEOUT_MS', '45000')}"
    )

    driver = IClassPro(
        base_url=args.base_url,
        save_screenshots=args.save_screenshots,
        storage_state_path=args.storage_state,
        deep_debug=args.deep_debug,
    )
    main_exception = None
    summary_data = []
    run_started_at = datetime.now(timezone.utc).isoformat()

    try:
        # --- Enrollment mode (Playwright browser) ---
        schedule_path = args.schedule
        logger.info(f"Mode: enrollment, schedule: {schedule_path}")
        with open(schedule_path, "r") as f:
            schedule = json.load(f)

        if schedule:
            logger.info(
                f"Schedule to process:\n{pd.DataFrame(schedule).drop(columns=['url', 'name', 'rowId'], errors='ignore').to_string(index=False)}"
            )

        driver.webdriver()
        driver.login(email=args.email, password=args.password)

        from iclasspro_jwt import clear_cart_before_enrollment

        clear_cart_before_enrollment(
            args.email,
            args.password,
            portal=args.portal or None,
            schedule=schedule,
        )

        for i, class_info in enumerate(schedule):
            log_info = {
                k: v
                for k, v in class_info.items()
                if k not in ("url", "name", "rowId")
            }
            class_started_at = datetime.now(timezone.utc).isoformat()
            logger.info(
                f"--- Processing class {i+1}/{len(schedule)}: \n{json.dumps(log_info, indent=4)} ---"
            )
            used_path = "auto"
            try:
                class_url = str(class_info.get("url") or "").strip()
                driver.enroll(
                    location=class_info.get("Location")
                    or class_info.get("location", ""),
                    timestr=class_info.get("Time") or class_info.get("time", ""),
                    daystr=class_info.get("Day") or class_info.get("day", ""),
                    student_id=args.student_id,
                    class_index=i,
                    class_url=class_url,
                )
                summary_data.append(
                    {
                        "class": class_info,
                        "status": "Success",
                        "error": "",
                        "path": used_path,
                        "started_at": class_started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except EnrollmentSkipped as skip:
                logger.info(f"Skipping class {class_info.get('name', '')}: {skip}")
                summary_data.append(
                    {
                        "class": class_info,
                        "status": "Skipped",
                        "error": str(skip),
                        "path": used_path,
                        "started_at": class_started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as e:
                logger.error(f"Failed to enroll in class {class_info}: {e}")
                summary_data.append(
                    {
                        "class": class_info,
                        "status": "Failed",
                        "error": str(e),
                        "path": used_path,
                        "started_at": class_started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                driver.take_screenshot(f"error_class_{i}.png")

        effective_complete_transaction = (
            args.complete_transaction and not args.dry_run
        )
        if args.dry_run and args.complete_transaction:
            logger.info(
                "Dry-run flag enabled; overriding complete-transaction setting."
            )
        driver.process_cart(
            promo_code=args.promo_code,
            complete_transaction=effective_complete_transaction,
        )
        logger.info("All operations completed.")

    except Exception as e:
        logger.critical(f"A critical error occurred: {e}")
        main_exception = e
    finally:
        driver.close()

        try:
            mode = "enrollment"
            counts = {
                "total": len(summary_data),
                "success": sum(1 for r in summary_data if r.get("status") == "Success"),
                "skipped": sum(1 for r in summary_data if r.get("status") == "Skipped"),
                "failed": sum(1 for r in summary_data if r.get("status") == "Failed"),
            }
            report = {
                "mode": mode,
                "started_at": run_started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "base_url": args.base_url,
                "schedule_path": getattr(args, "schedule", None),
                "complete_transaction": (
                    args.complete_transaction and not args.dry_run
                ),
                "summary": counts,
                "results": summary_data,
                "critical_error": str(main_exception) if main_exception else None,
            }
            with open(args.report_path, "w") as rf:
                json.dump(report, rf, indent=2)
            logger.info(f"Run report written to {args.report_path}.")
        except Exception as report_err:
            logger.warning(f"Failed to write run report: {report_err}")

        if args.send_email:
            logger.info("Email sending is enabled. Checking credentials...")
            to_addr = args.email
            from_addr = args.email
            app_password = os.getenv("ICLASS_EMAIL_APP_PASSWORD")
            smtp_server = os.getenv("ICLASS_SMTP_SERVER")
            smtp_port = int(os.getenv("ICLASS_SMTP_PORT", 587))

            if all([to_addr, from_addr, app_password, smtp_server]):
                send_log_email(
                    log_file,
                    to_addr,
                    from_addr,
                    app_password,
                    smtp_server,
                    smtp_port,
                    summary_data=summary_data,
                )
            else:
                logger.warning(
                    "Cannot send log email. Missing one or more required environment variables."
                )
        logger.info("Script finished.")


if __name__ == "__main__":
    main()
