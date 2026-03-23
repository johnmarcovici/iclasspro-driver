#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

load_dotenv(override=True)


# --- Email Function ---
def send_log_email(
    log_file_path,
    to_addr,
    from_addr,
    app_password,
    smtp_server,
    smtp_port,
    subject_status,
):
    """Reads the log file and sends its content in an email."""
    try:
        with open(log_file_path, "r") as f:
            log_content = f.read()

        msg = MIMEMultipart()
        msg["Subject"] = f"iClassPro Enrollment Log ({subject_status})"
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.attach(MIMEText(log_content, "plain"))

        logging.info("Connecting to SMTP server to send log email...")
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(from_addr, app_password)
            server.send_message(msg)
        logging.info("Log email sent successfully.")

    except Exception as e:
        logging.error(f"Failed to send log email: {e}", exc_info=True)


class IClassPro:
    def __init__(self, base_url: str = "", save_screenshots: bool = False):
        self.base_url = base_url
        self.save_screenshots = save_screenshots
        self.playwright = None
        self.browser = None
        self.page = None

    async def take_screenshot(self, filename: str, full_page: bool = False):
        if self.save_screenshots and self.page:
            os.makedirs("screenshots", exist_ok=True)
            await self.page.screenshot(
                path=os.path.join("screenshots", filename), full_page=full_page
            )

    async def init_system(self) -> None:
        self.playwright = await async_playwright().start()
        headless = os.getenv("ICLASS_HEADLESS", "1").lower() not in ("0", "false", "no")
        self.browser = await self.playwright.chromium.launch(headless=headless)
        self.page = await self.browser.new_page()
        await Stealth().apply_stealth_async(self.page)

    async def login(self, email: str = "", password: str = "") -> None:
        logging.info("Navigating to login page...")
        await self.page.goto(self.base_url.rstrip("/") + "/login?showLogin=1")
        await self.page.wait_for_selector("#email")
        await self.page.fill("#email", email)
        await self.page.fill("#password", password)
        await self.page.press("#password", "Enter")
        await self.page.wait_for_selector("text=/My Account/i", timeout=15000)
        logging.info("Login successful.")

    async def select_student(self, student_id: str):
        logging.info(f"Selecting student {student_id}...")
        # This is a simplified version. A real implementation would navigate pages.
        await self.page.goto(
            self.base_url.rstrip("/") + f"/student/{student_id}/dashboard"
        )
        await self.page.wait_for_load_state("networkidle")
        logging.info("Student selected.")

    async def enroll(
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

        await self.page.goto(booking_url, wait_until="load")
        await self.take_screenshot(f"classes_page_{class_index}.png", full_page=True)

        class_link = self.page.locator(f"a:has-text('at {timestr}')")
        try:
            await class_link.first.wait_for(state="visible", timeout=20000)
            count = await class_link.count()
            logging.info(
                f"Found {count} class link(s) for {timestr}. Clicking the latest one to enroll."
            )
            await class_link.last.click()
        except Exception:
            raise RuntimeError(
                f"Could not find class at {timestr} within the time limit."
            )

        enroll_now_button = self.page.locator("button:has-text('Enroll Now')")
        await enroll_now_button.wait_for(state="visible", timeout=15000)
        await enroll_now_button.click()

        add_to_cart_button = self.page.locator("button:has-text('Add to Cart')")
        await add_to_cart_button.wait_for(state="visible", timeout=15000)
        await add_to_cart_button.click()

        await self.take_screenshot(f"after_add_to_cart_{class_index}.png", full_page=True)

    async def enroll_from_direct_link(self, link: str) -> None:
        await self.page.goto(link)
        await self.page.click("button:has-text('Enroll Now')")
        await self.page.click("button:has-text('Add to Cart')")
        # In a real scenario, we'd wait for confirmation here
        await asyncio.sleep(2)

    async def process_cart(
        self, promo_code: str = "", complete_transaction: bool = False
    ) -> None:
        """Navigates to the cart and completes checkout."""
        logging.info("Processing cart...")
        cart_url = self.base_url.rstrip("/") + "/cart"
        await self.page.goto(cart_url, wait_until="load")
        await self.page.wait_for_timeout(5000)

        await self.take_screenshot("cart_final.png", full_page=True)

        if promo_code:
            logging.info(f"Applying promo code: {promo_code}")
            await self.page.locator("a:has-text('Use Promo Code')").click()
            await self.page.wait_for_timeout(1000)
            await self.page.locator("[name='promoCode']").fill(promo_code)
            await self.page.locator("button:has-text('Apply')").click()
            await self.page.wait_for_timeout(2000)

        if complete_transaction:
            logging.info("Attempting to complete transaction...")
            await self.page.locator("button:has-text('Complete Transaction')").click()
            logging.info("Waiting 15 seconds for transaction to finalize...")
            await self.page.wait_for_timeout(15000)
            await self.take_screenshot("transaction_complete.png", full_page=True)
            logging.info("Transaction submitted.")
        else:
            logging.info("Dry run enabled. Skipping final transaction completion.")

        logging.info("Cart processing complete.")

    async def close(self):
        if self.browser:
            await self.browser.close()
            logging.info("Browser closed.")
        if self.playwright:
            await self.playwright.stop()
            logging.info("Playwright stopped.")


async def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="iClassPro Enrollment Bot")
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("ICLASS_BASE_URL", "https://portal.iclasspro.com/scaq/"),
        help="Portal base URL",
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--schedule",
        type=str,
        default=os.getenv("ICLASS_SCHEDULE", "schedules/schedule.json"),
        help="Path to the schedule JSON file",
    )
    group.add_argument(
        "--scraped-data", help="Path to a JSON file with pre-scraped class data"
    )
    args = parser.parse_args()

    # --- Setup Logging ---
    log_file = "iclasspro.log"
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    file_handler = logging.FileHandler(log_file, mode="w")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if not all([args.email, args.password, args.student_id]):
        logging.error(
            "Missing required environment variables or arguments: ICLASS_EMAIL, ICLASS_PASSWORD, ICLASS_STUDENT_ID"
        )
        exit(1)

    logging.info(f"Starting iClassPro enrollment bot for email: {args.email}")
    logging.info(f"Student ID: {args.student_id}")

    driver = IClassPro(base_url=args.base_url, save_screenshots=args.save_screenshots)
    main_exception = None

    try:
        await driver.init_system()
        await driver.login(email=args.email, password=args.password)

        if args.scraped_data:
            with open(args.scraped_data, "r") as f:
                scraped_classes = json.load(f)
            for i, class_info in enumerate(scraped_classes):
                logging.info(
                    f"--- Enrolling from scraped data {i+1}/{len(scraped_classes)}: {class_info} ---"
                )
                try:
                    await driver.enroll_from_direct_link(class_info["link"])
                except Exception as e:
                    logging.error(
                        f"Failed to enroll in class {class_info}: {e}", exc_info=True
                    )
        else:
            with open(args.schedule, "r") as f:
                schedule = json.load(f)
            for i, class_info in enumerate(schedule):
                logging.info(
                    f"--- Processing class {i+1}/{len(schedule)}: {class_info} ---"
                )
                try:
                    await driver.enroll(
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
                    await driver.take_screenshot(f"error_class_{i}.png")

        await driver.process_cart(
            promo_code=args.promo_code,
            complete_transaction=args.complete_transaction,
        )
        logging.info("All operations completed.")

    except Exception as e:
        logging.critical(f"A critical error occurred: {e}", exc_info=True)
        main_exception = e
    finally:
        await driver.close()

        if args.send_email:
            logging.info("Email sending is enabled. Checking credentials...")
            to_addr = args.email
            from_addr = args.email
            app_password = os.getenv("ICLASS_EMAIL_APP_PASSWORD")
            smtp_server = os.getenv("ICLASS_SMTP_SERVER")
            smtp_port = int(os.getenv("ICLASS_SMTP_PORT", 587))

            if all([to_addr, from_addr, app_password, smtp_server]):
                status = "FAILURE" if main_exception else "SUCCESS"
                send_log_email(
                    log_file,
                    to_addr,
                    from_addr,
                    app_password,
                    smtp_server,
                    smtp_port,
                    status,
                )
            else:
                logging.warning(
                    "Cannot send log email. Missing one or more required environment variables."
                )
        logging.info("Script finished.")


if __name__ == "__main__":
    asyncio.run(main())
