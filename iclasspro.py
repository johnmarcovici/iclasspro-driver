#!/usr/bin/env python3

import argparse
import json
import logging
import os
import re
import time

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# --- Basic Setup ---
# Load environment variables from .env file
load_dotenv(override=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class iClassPro:
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

        logging.info(f"Launching browser (headless={headless}, slow_mo={slow_mo}ms)")
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
        logging.info("Browser launched successfully.")

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
                    logging.debug(f"Found {count} cart items with selector '{sel}'.")
                    return count
        except Exception as e:
            logging.warning(f"Could not get cart item count from DOM: {e}")
        return 0

    def _wait_for_cart_item_count(
        self, min_count: int = 1, timeout: int = 15000
    ) -> int:
        """Wait until the cart appears to have at least `min_count` items."""
        logging.info(f"Waiting for cart to contain at least {min_count} item(s)...")
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            count = self._get_cart_item_count()
            if count >= min_count:
                logging.info(f"Cart item count is now {count}.")
                return count
            time.sleep(0.5)
        logging.warning("Timeout reached while waiting for cart item count.")
        return 0

    def login(self, email: str = "", password: str = "") -> None:
        """Logs into the iClassPro portal."""
        logging.info("Navigating to login page...")
        login_url = self.base_url.rstrip("/") + "/login?showLogin=1"
        self.page.goto(login_url, wait_until="load")
        self.page.wait_for_timeout(5000)
        self.take_screenshot("01_after_goto_login.png")

        # Handle "Select a location" modal if it appears
        try:
            location_button = self.page.locator("span:has-text('SCAQ')")
            if location_button.is_visible(timeout=5000):
                logging.info("Selecting location 'SCAQ'.")
                location_button.click()
                self.page.wait_for_timeout(2000)
                self.page.wait_for_selector('input[type="email"]', timeout=10000)
        except Exception as e:
            logging.debug(f"Location selection modal not found, proceeding. Error: {e}")

        # Handle "Are you a current customer?" modal
        try:
            yes_button = self.page.locator("button:has-text('Yes')")
            if yes_button.is_visible(timeout=5000):
                logging.info("Clicking 'Yes' on 'current customer' modal.")
                self.take_screenshot("02_before_clicking_yes.png")
                yes_button.click()
                self.page.wait_for_timeout(1000)
                self.take_screenshot("03_after_clicking_yes.png")
        except Exception:
            logging.debug("'Current customer' modal not found, proceeding.")

        # Fill credentials
        logging.info("Entering login credentials.")
        self.take_screenshot("04_before_login_attempt.png")
        self.page.locator("#email").fill(email)
        self.page.locator("#password").fill(password)
        self.take_screenshot("05_after_filling_credentials.png")
        self.page.locator("#password").press("Enter")

        # Wait for successful login (e.g., by checking for a dashboard element)
        try:
            self.page.wait_for_selector("text=/My Account/i", timeout=15000)
            logging.info("Login successful.")
        except Exception:
            logging.error("Login failed. Could not find 'My Account' text after login.")
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
        logging.info(f"Searching for class: {daystr} at {timestr} in {location}")
        days = [
            "sunday",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
        ]
        day_query = f"&days={days.index(daystr.lower()) + 1}"
        student_query = f"&selectedStudents={student_id}"
        booking_url = f"{self.base_url}classes?q={location.replace(' ', '%20')}{day_query}{student_query}"

        self.page.goto(booking_url, wait_until="load")
        self.take_screenshot(f"classes_page_{class_index}.png", full_page=True)

        # Find the link for the class time
        class_link = self.page.locator(f"a:has-text('at {timestr}')")
        if class_link.count() == 0:
            raise RuntimeError(f"Could not find class at {timestr}.")

        logging.info(f"Found class link for {timestr}. Clicking to enroll.")
        class_link.first.click()

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
        self._wait_for_cart_item_count(min_count=initial_cart_count + 1)

        self.take_screenshot(f"after_add_to_cart_{class_index}.png", full_page=True)

    def _wait_for_cart_to_empty(self, timeout: int = 90000) -> None:
        """Wait until the cart shows 0 items."""
        logging.info("Waiting for cart to empty...")
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            count = self._get_cart_item_count()
            if count == 0:
                logging.info("Cart is now empty. Transaction likely successful.")
                return
            time.sleep(1)  # Check every second
        logging.error("Timeout reached while waiting for cart to empty.")
        raise RuntimeError("Transaction did not complete within the time limit.")

    def process_cart(
        self, promo_code: str = "", complete_transaction: bool = False
    ) -> None:
        """Navigates to the cart and completes checkout."""
        logging.info("Processing cart...")
        cart_url = self.base_url.rstrip("/") + "/cart"
        self.page.goto(cart_url, wait_until="load")
        self.page.wait_for_timeout(5000)  # Extra wait for cart to render fully

        self.take_screenshot("cart_final.png", full_page=True)

        if self._get_cart_item_count() == 0:
            logging.warning("Cart is empty. Nothing to process.")
            return

        if promo_code:
            logging.info(f"Applying promo code: {promo_code}")
            self.page.locator("a:has-text('Use Promo Code')").click()
            self.page.wait_for_timeout(1000)
            self.page.locator("[name='promoCode']").fill(promo_code)
            self.page.locator("button:has-text('Apply')").click()
            self.page.wait_for_timeout(2000)

        if complete_transaction:
            logging.info("Attempting to complete transaction...")
            self.page.locator("button:has-text('Complete Transaction')").click()
            self._wait_for_cart_to_empty()
            self.take_screenshot("transaction_complete.png", full_page=True)
        else:
            logging.info("Dry run enabled. Skipping final transaction completion.")

        logging.info("Cart processing complete.")

    def close(self):
        """Safely close the browser and Playwright instances."""
        if self.browser:
            self.browser.close()
            logging.info("Browser closed.")
        if self.playwright:
            self.playwright.stop()
            logging.info("Playwright stopped.")


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
    args = parser.parse_args()

    if not all([args.email, args.password, args.student_id]):
        logging.error(
            "Missing required environment variables or arguments: ICLASS_EMAIL, ICLASS_PASSWORD, ICLASS_STUDENT_ID"
        )
        exit(1)

    logging.info(f"Starting iClassPro enrollment bot for email: {args.email}")
    logging.info(f"Password: {args.password}")
    logging.info(f"Student ID: {args.student_id}")
    logging.info(f"Using schedule: {args.schedule}")

    driver = iClassPro(base_url=args.base_url, save_screenshots=args.save_screenshots)
    try:
        with open(args.schedule, "r") as f:
            schedule = json.load(f)
    except FileNotFoundError:
        logging.error(f"Schedule file not found at {args.schedule}")
        exit(1)

    try:
        driver.webdriver()
        driver.login(email=args.email, password=args.password)

        for i, class_info in enumerate(schedule):
            logging.info(
                f"--- Processing class {i+1}/{len(schedule)}: {class_info} ---"
            )
            try:
                driver.enroll(
                    location=class_info["Location"],
                    timestr=class_info["Time"],
                    daystr=class_info["Day"],
                    student_id=args.student_id,
                    class_index=i,
                )
            except Exception as e:
                logging.error(
                    f"Failed to enroll in class {class_info}: {e}", exc_info=True
                )
                driver.take_screenshot(f"error_class_{i}.png")

        driver.process_cart(
            promo_code=args.promo_code,
            complete_transaction=args.complete_transaction,
        )
        logging.info("All operations completed.")

    except Exception as e:
        logging.critical(f"A critical error occurred: {e}", exc_info=True)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
