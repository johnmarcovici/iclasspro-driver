#!/usr/bin/env python3

import argparse
import json
import logging
import os
import re
import smtplib
import time
import pandas as pd
import yaml
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse, urljoin, quote

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

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
            total_count = len(summary_data)
            if success_count == total_count:
                subject_text = f"Added {success_count} of {total_count} classes"
            else:
                subject_text = (
                    f"Enrollment Report: {success_count}/{total_count} Successful"
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

    def __init__(self, base_url: str = "", save_screenshots: bool = False):
        self.base_url = base_url
        self.save_screenshots = save_screenshots
        self.playwright = None
        self.browser = None
        self.page = None
        self._last_cart_response = None

    def take_screenshot(self, filename: str, full_page: bool = False):
        """Helper to save a screenshot if --save-screenshots is enabled."""
        if self.save_screenshots and self.page:
            os.makedirs("screenshots", exist_ok=True)
            self.page.screenshot(
                path=os.path.join("screenshots", filename), full_page=full_page
            )

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
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        # Add anti-bot-detection scripts
        self.context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            """
        )
        self.page = self.context.new_page()
        Stealth().apply_stealth_sync(self.page)
        logger.info("Browser launched successfully.")

    def _get_cart_item_count(self) -> int:
        """Return an estimated number of cart items from the DOM."""
        try:
            selectors = [
                ".products-wrap .list-group-item",
                ".cart-item",
                ".cartItem",
                ".cart__item",
                "[role='listitem']",
            ]
            for sel in selectors:
                count = self.page.locator(sel).count()
                if count > 0:
                    logger.debug(f"Found {count} cart items with selector '{sel}'.")
                    return count
        except Exception as e:
            logger.warning(f"Could not get cart item count from DOM: {e}")
        return 0

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

    def login(self, email: str = "", password: str = "") -> None:
        """Logs into the iClassPro portal."""
        logger.info("Navigating to login page...")
        login_url = self.base_url.rstrip("/") + "/login?showLogin=1"
        self.page.goto(login_url, wait_until="load")
        self.page.wait_for_timeout(5000)
        self.take_screenshot("01_after_goto_login.png")

        # Handle "Select a location" modal if it appears
        try:
            location_button = self.page.locator("span:has-text('SCAQ')")
            if location_button.is_visible(timeout=5000):
                logger.info("Selecting location 'SCAQ'.")
                location_button.click()
                self.page.wait_for_timeout(2000)
                self.page.wait_for_selector('input[type="email"]', timeout=10000)
        except Exception as e:
            logger.debug(f"Location selection modal not found, proceeding. Error: {e}")

        # Handle "Are you a current customer?" modal
        try:
            yes_button = self.page.locator("button:has-text('Yes')")
            if yes_button.is_visible(timeout=5000):
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
        self.page.locator("#email").fill(email)
        self.page.locator("#password").fill(password)
        self.take_screenshot("05_after_filling_credentials.png")
        self.page.locator("#password").press("Enter")

        # Wait for successful login (e.g., by checking for a dashboard element)
        try:
            self.page.wait_for_selector("text=/My Account/i", timeout=15000)
            logger.info("Login successful.")
        except Exception:
            logger.error("Login failed. Could not find 'My Account' text after login.")
            self.take_screenshot("login_failure.png")
            raise RuntimeError("Login failed. Please check credentials and portal URL.")

    def enroll(
        self,
        location: str,
        timestr: str,
        daystr: str,
        student_id: int,
        class_index: int,
    ) -> None:
        """Finds and adds a single class to the cart."""
        logger.info(f"Searching for class: {daystr} at {timestr} in {location}")
        day_index = DAY_TO_QUERY_INDEX.get(daystr.strip().lower())
        if day_index is None:
            valid_days = ", ".join(WEEK_DAYS)
            raise ValueError(f"Invalid day '{daystr}'. Expected one of: {valid_days}")

        day_query = f"&days={day_index}"
        student_query = f"&selectedStudents={student_id}"
        booking_url = f"{self.base_url}classes?q={location.replace(' ', '%20')}{day_query}{student_query}"

        self.page.goto(booking_url, wait_until="load")
        self.take_screenshot(f"classes_page_{class_index}.png", full_page=True)

        # Find the link for the class time, waiting for it to appear
        class_link = self.page.locator(f"a:has-text('at {timestr}')")
        try:
            # Wait for at least one to be visible
            class_link.first.wait_for(state="visible", timeout=20000)

            # If there are multiple (e.g., this week and next week), pick the last one
            count = class_link.count()
            logger.info(
                f"Found {count} class link(s) for {timestr}. Clicking the latest one to enroll."
            )
            class_link.last.click()
        except Exception:
            raise RuntimeError(
                f"Could not find class at {timestr} within the time limit."
            )

        # Wait for the "Enroll Now" button to be ready and click it
        enroll_now_button = self.page.locator("button:has-text('Enroll Now')")
        enroll_now_button.wait_for(state="visible", timeout=15000)
        enroll_now_button.click()

        # Get the cart count *before* adding the new class
        initial_cart_count = self._get_cart_item_count()

        # Wait for the "Add to Cart" button to be ready and click it
        add_to_cart_button = self.page.locator("button:has-text('Add to Cart')")
        add_to_cart_button.wait_for(state="visible", timeout=15000)
        add_to_cart_button.click()

        # Wait for the cart item count to increase
        final_cart_count = self._wait_for_cart_item_count(
            min_count=initial_cart_count + 1
        )
        if final_cart_count < initial_cart_count + 1:
            raise RuntimeError("Failed to verify that the class was added to the cart.")

        self.take_screenshot(f"after_add_to_cart_{class_index}.png", full_page=True)

    def process_cart(
        self, promo_code: str = "", complete_transaction: bool = False
    ) -> None:
        """Navigates to the cart and completes checkout."""
        logger.info("Processing cart...")
        cart_url = self.base_url.rstrip("/") + "/cart"
        self.page.goto(cart_url, wait_until="load")
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
            self.page.locator("button:has-text('Complete Transaction')").click()
            logger.info("Waiting 15 seconds for transaction to finalize...")
            self.page.wait_for_timeout(15000)
            self.take_screenshot("transaction_complete.png", full_page=True)
            logger.info("Transaction submitted.")
        else:
            logger.info("Dry run enabled. Skipping final transaction completion.")

        logger.info("Cart processing complete.")

    def scrape_classes(
        self,
        student_id: int,
        days_filter: list = None,
        locations_filter: list = None,
    ) -> list:
        """Scrape available classes from the portal, iterating over each day.

        Args:
            student_id: Portal student ID.
            days_filter: Optional list of day names (e.g. ["Sunday", "Monday"]) to
                limit which days are fetched.  When None or empty, all 7 days are
                scraped.
            locations_filter: Optional list of location strings (e.g. ["Culver",
                "El Segundo"]) to keep.  When None or empty, all locations are kept.
        """
        discovered = []
        # Deduplicate by (day, time, name) rather than by href/URL, because
        # JS-navigated class cards often share a placeholder href (e.g. "#") which
        # would cause every class after the first to be dropped as a "duplicate".
        seen_keys = set()

        # Normalise filter lists so comparisons are case-insensitive
        days_filter_norm = (
            [d.strip().lower() for d in days_filter if d.strip()] if days_filter else []
        )
        locations_filter_norm = (
            [l.strip().lower() for l in locations_filter if l.strip()]
            if locations_filter
            else []
        )

        days_to_scrape = [
            (idx, name)
            for idx, name in enumerate(WEEK_DAYS, start=1)
            if not days_filter_norm or name.lower() in days_filter_norm
        ]
        if days_filter_norm:
            logger.info(f"Day filter active: scraping {[n for _, n in days_to_scrape]}")
        if locations_filter_norm:
            logger.info(f"Location filter active: keeping {locations_filter_norm}")

        for day_idx, day_name in days_to_scrape:
            logger.info(f"Scraping classes for {day_name}...")
            url = f"{self.base_url}classes?days={day_idx}&selectedStudents={student_id}"
            self.page.goto(url, wait_until="load")
            self.page.wait_for_timeout(2000)
            # Scroll to the bottom so any lazily-rendered class cards are added to the DOM
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(1000)
            self.take_screenshot(f"scrape_{day_name.lower()}.png", full_page=True)

            # Strategy: iterate every anchor on the page and keep only those
            # whose text contains a time pattern (e.g. "at 10:30am").  These
            # are the card-wrapper anchors that carry the class name/time in
            # their text_content().  They typically have href="/scaq" (SPA
            # root) so the class ID cannot be read from their own href.
            # Instead, for each such anchor we search the nearest parent
            # container (walking up the DOM) for a sibling "View Available
            # Dates" link whose href does contain the class-details path.
            all_anchors = self.page.locator("a").all()
            for anchor in all_anchors:
                try:
                    text = (anchor.text_content() or "").strip()
                    href = anchor.get_attribute("href") or ""

                    # Only process links whose text contains a time pattern
                    time_match = _TIME_RE.search(text)
                    if not time_match or not href:
                        continue

                    # Skip bare "at TIME" links (navigation/filter anchors).
                    # Real class-card links always have content before the time.
                    text_prefix = _TIME_RE.sub("", text).strip()
                    if not text_prefix:
                        continue

                    # --- Build the class detail URL ---
                    # First check if this anchor's own href already has the ID.
                    # If not, traverse upward in the DOM to find a sibling
                    # "class-details" link that carries the numeric class ID.
                    class_id_match = re.search(r"/class-details/(\d+)", href)
                    if not class_id_match:
                        details_href = anchor.evaluate(
                            r"""el => {
                            let node = el.parentElement;
                            while (node && node.tagName !== 'BODY') {
                                const links = node.querySelectorAll(
                                    "a[href*='class-details']"
                                );
                                for (const lnk of links) {
                                    const h = lnk.getAttribute('href') || '';
                                    if (/\/class-details\/\d+/.test(h)) return h;
                                }
                                node = node.parentElement;
                            }
                            return '';
                        }"""
                        )
                        class_id_match = re.search(
                            r"/class-details/(\d+)", details_href
                        )

                    if class_id_match:
                        filters_json = json.dumps(
                            {"students": str(student_id), "days": str(day_idx)}
                        )
                        class_url = (
                            f"{self.base_url.rstrip('/')}/class-details/{class_id_match.group(1)}"
                            f"?selectedStudents={student_id}&filters={quote(filters_json)}"
                        )
                    elif href.startswith("http"):
                        class_url = href
                    else:
                        class_url = urljoin(self.base_url, href)

                    time_str = time_match.group(1)

                    # --- Extract the class title (name) ---
                    # The card anchor's text_content() includes the full card:
                    #   "Location:Day MM/DD at TIME - CourseType Day|HH:MM AM – HH:MM AM
                    #    View Available Dates SMTWTFS44 Open"
                    # Primary: look for a heading-like element inside the anchor.
                    # Fallback: split on the "DayAbbr|" metadata separator or keywords.
                    name = ""
                    try:
                        heading_el = anchor.locator(
                            "h1, h2, h3, h4, h5, h6, strong, b"
                        ).first
                        candidate = (heading_el.text_content() or "").strip()
                        if candidate and _TIME_RE.search(candidate):
                            name = candidate
                    except Exception:
                        pass

                    if not name:
                        for split_pat in (
                            r"\s+(?:Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s*\|",
                            r"\s+(?:Available\b|View\b)",
                        ):
                            parts = re.split(
                                split_pat, text, maxsplit=1, flags=re.IGNORECASE
                            )
                            if len(parts) > 1:
                                name = parts[0].strip()
                                break

                    if not name:
                        name = text_prefix or text

                    location = ""

                    # --- Location extraction ---
                    colon_idx = name.find(":")
                    if colon_idx > 0:
                        prefix = name[:colon_idx].strip()
                        for loc in self.KNOWN_LOCATIONS:
                            if (
                                loc.lower() == prefix.lower()
                                or loc.lower() in prefix.lower()
                            ):
                                location = loc
                                break

                    if not location:
                        for loc in self.KNOWN_LOCATIONS:
                            if loc.lower() in name.lower():
                                location = loc
                                break

                    # Apply location filter (if requested)
                    if (
                        locations_filter_norm
                        and location.lower() not in locations_filter_norm
                    ):
                        continue

                    # Deduplicate by (day, time, name)
                    dedup_key = (day_name, time_str, name)
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)

                    discovered.append(
                        {
                            "name": name,
                            "Location": location,
                            "Day": day_name,
                            "Time": time_str,
                            "url": class_url,
                        }
                    )
                    logger.info(
                        f"  Found: {day_name} at {time_str} — {name}"
                        f" ({location or 'location unknown'})"
                    )
                except Exception as e:
                    logger.debug(f"Error processing anchor: {e}")

        logger.info(f"Discovery complete. Found {len(discovered)} class(es).")
        return discovered

    def enroll_by_url(self, url: str, class_index: int) -> None:
        """Navigate directly to a class URL and add it to the cart."""
        logger.info(f"Enrolling via direct URL: {url}")
        self.page.goto(url, wait_until="load")
        self.take_screenshot(f"classes_page_url_{class_index}.png", full_page=True)

        # Wait for and click "Enroll Now"
        enroll_now_button = self.page.locator("button:has-text('Enroll Now')")
        enroll_now_button.wait_for(state="visible", timeout=15000)
        enroll_now_button.click()

        # Get initial cart count before adding
        initial_cart_count = self._get_cart_item_count()

        # Wait for and click "Add to Cart"
        add_to_cart_button = self.page.locator("button:has-text('Add to Cart')")
        add_to_cart_button.wait_for(state="visible", timeout=15000)
        add_to_cart_button.click()

        # Verify cart count increased
        final_cart_count = self._wait_for_cart_item_count(
            min_count=initial_cart_count + 1
        )
        if final_cart_count < initial_cart_count + 1:
            raise RuntimeError("Failed to verify that the class was added to the cart.")

        self.take_screenshot(f"after_add_to_cart_url_{class_index}.png", full_page=True)

    def close(self):
        """Safely close the browser and Playwright instances."""
        if self.browser:
            self.browser.close()
            logger.info("Browser closed.")
        if self.playwright:
            self.playwright.stop()
            logger.info("Playwright stopped.")


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="iClassPro Enrollment Bot")
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
        help="Scrape available classes and print as JSON instead of enrolling.",
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
    args = parser.parse_args()

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

    driver = IClassPro(base_url=args.base_url, save_screenshots=args.save_screenshots)
    main_exception = None
    summary_data = []

    try:
        if args.scrape:
            # --- Scrape mode: discover available classes and emit JSON ---
            logger.info("Mode: scrape available classes")
            days_filter = [d.strip() for d in args.scrape_days.split(",") if d.strip()]
            locations_filter = [
                l.strip() for l in args.scrape_locations.split(",") if l.strip()
            ]
            driver.webdriver()
            driver.login(email=args.email, password=args.password)
            classes = driver.scrape_classes(
                student_id=args.student_id,
                days_filter=days_filter or None,
                locations_filter=locations_filter or None,
            )
            # Emit a single parseable line that the web UI will detect
            print(f"CLASSES_JSON:{json.dumps(classes)}", flush=True)
            logger.info("All operations completed.")

        else:
            # --- Enrollment mode ---
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

            for i, class_info in enumerate(schedule):
                log_info = {
                    k: v
                    for k, v in class_info.items()
                    if k not in ("url", "name", "rowId")
                }
                logger.info(
                    f"--- Processing class {i+1}/{len(schedule)}: \n{json.dumps(log_info, indent=4)} ---"
                )
                try:
                    driver.enroll(
                        location=class_info.get("Location")
                        or class_info.get("location", ""),
                        timestr=class_info.get("Time") or class_info.get("time", ""),
                        daystr=class_info.get("Day") or class_info.get("day", ""),
                        student_id=args.student_id,
                        class_index=i,
                    )
                    summary_data.append(
                        {"class": class_info, "status": "Success", "error": ""}
                    )
                except Exception as e:
                    logger.error(f"Failed to enroll in class {class_info}: {e}")
                    summary_data.append(
                        {"class": class_info, "status": "Failed", "error": str(e)}
                    )
                    driver.take_screenshot(f"error_class_{i}.png")

            driver.process_cart(
                promo_code=args.promo_code,
                complete_transaction=args.complete_transaction,
            )
            logger.info("All operations completed.")

    except Exception as e:
        logger.critical(f"A critical error occurred: {e}")
        main_exception = e
    finally:
        driver.close()

        if args.send_email and not args.scrape:
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
