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
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class iClassPro:
    def __init__(self, base_url: str = ""):
        self.base_url = base_url
        self.playwright = None
        self.browser = None
        self.page = None
        self._last_cart_response = None

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
            selectors = [".products-wrap .list-group-item", ".cart-item", ".cartItem", ".cart__item", "[role='listitem']"]
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
        self.page.screenshot(path="01_after_goto_login.png")

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
                self.page.screenshot(path="02_before_clicking_yes.png")
                yes_button.click()
                self.page.wait_for_timeout(1000)
                self.page.screenshot(path="03_after_clicking_yes.png")
        except Exception:
            logging.debug("'Current customer' modal not found, proceeding.")

        # Fill credentials
        logging.info("Entering login credentials.")
        self.page.screenshot(path="04_before_login_attempt.png")
        self.page.locator("#email").fill(email)
        self.page.locator("#password").fill(password)
        self.page.screenshot(path="05_after_filling_credentials.png")
        self.page.locator("#password").press("Enter")

        # Wait for successful login (e.g., by checking for a dashboard element)
        try:
            self.page.wait_for_selector("text=/My Account/i", timeout=15000)
            logging.info("Login successful.")
        except Exception:
            logging.error("Login failed. Could not find 'My Account' text after login.")
            self.page.screenshot(path="login_failure.png")
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
        self.page.screenshot(path=f"classes_page_{class_index}.png", full_page=True)

        # Find the link for the class time
        class_link = self.page.locator(f"a:has-text('at {timestr}')")
        if class_link.count() == 0:
            raise RuntimeError(f"Could not find class at {timestr}.")

        logging.info(f"Found class link for {timestr}. Clicking to enroll.")
        class_link.first.click()

        # Click "Enroll Now"
        self.page.locator("button:has-text('Enroll Now')").click()
        self.page.wait_for_timeout(1000)

        # Click "Add to Cart"
        self.page.locator("button:has-text('Add to Cart')").click()

        # Check for "already enrolled" or "added to cart" popups
        try:
            # Check for either message, whichever appears first
            self.page.wait_for_selector(
                "text=/is already enrolled|added to cart/i", timeout=10000
            )
            logging.info("Confirmed enrollment or item already in cart.")
        except Exception:
            logging.warning("Did not see confirmation toast for cart add.")

        self.page.screenshot(
            path=f"after_add_to_cart_{class_index}.png", full_page=True
        )

    def process_cart(self, promo_code: str = "", complete_transaction: bool = False) -> None:
        """Navigates to the cart and completes checkout."""
        logging.info("Processing cart...")
        cart_url = self.base_url.rstrip("/") + "/cart"
        self.page.goto(cart_url, wait_until="load")
        self.page.wait_for_timeout(5000)  # Extra wait for cart to render fully

        self.page.screenshot(path="cart_final.png", full_page=True)
        logging.info("Saved final cart screenshot to cart_final.png")

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
            logging.info("Proceeding to checkout...")
            self.page.locator("button:has-text('Complete Transaction')").click()
            # Final steps of checkout would go here
        else:
            logging.info("Dry run: Skipping 'Complete Transaction' button click.")

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
        help="If set, actually complete the transaction by clicking the 'Complete Transaction' button.",
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

    driver = iClassPro(base_url=args.base_url)
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
                driver.page.screenshot(path=f"error_class_{i}.png")

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
