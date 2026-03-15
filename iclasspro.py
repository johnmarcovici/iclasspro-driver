#!/usr/bin/env python3

import argparse
import json
import os

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Playwright Imports
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext


class iClassPro:
    def __init__(self, base_url: str = ""):
        self.base_url = base_url
        self.playwright = None
        self.browser = None
        self.page = None

    def webdriver(self) -> None:
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.page = self.browser.new_page()

    def find_element(
        self,
        selector: str = "",
        filterstr: str = "",
        index: int = 0,
        timeout: int = 10000,
    ):
        """Return a locator for the matching element."""
        locator = self.page.locator(selector)
        if filterstr:
            locator = locator.filter(has_text=filterstr)

        try:
            count = locator.count()
            if count == 0:
                return None

            # Support negative indices like Python lists
            if index < 0:
                index = count + index
            if index < 0 or index >= count:
                return None

            element = locator.nth(index)
            element.wait_for(state="visible", timeout=timeout)
            return element
        except Exception:
            return None

    def click(
        self, element=None, selector: str = "", filterstr: str = "", **kwargs
    ) -> None:
        if element is None:
            element = self.find_element(
                selector=selector, filterstr=filterstr, **kwargs
            )

        if element:
            element.click()

    def send_keys(
        self,
        element=None,
        key: str = "",
        enter: bool = False,
        selector: str = "",
        filterstr: str = "",
        timeout: int = 10000,
        **kwargs,
    ) -> None:
        if element is None:
            element = self.find_element(
                selector=selector, filterstr=filterstr, **kwargs
            )

        if element:
            element.wait_for(state="visible", timeout=timeout)
            element.fill(key)
            if enter:
                element.press("Enter")

    def login(self, location: str = "", email: str = "", password: str = "") -> None:
        # Navigate to login
        self.page.goto(self.base_url + "login")
        self.page.wait_for_load_state("networkidle")

        try:
            # A location prompt may pop up
            self.click(selector="button", filterstr=location)
        except Exception:
            pass

        # Process the login page: click Yes for account, then enter username and password
        self.click(selector="button", filterstr="Yes")
        self.send_keys(key=email, selector="[name='email']")
        self.send_keys(key=password, enter=True, selector="[name='password']")

    def enroll(
        self,
        location: str = "",
        timestr: str = "",
        daystr: str = "",
        student_id: int = 0,
        next_week: bool = False,
    ) -> None:
        # Form booking page URL including location, day, and student query
        location_query = "q=" + location.replace(" ", "%20")
        days = [
            "sunday",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
        ]
        day_query = "&days=" + str(days.index(daystr.split(" ")[0].lower()) + 1)
        student_query = "&selectedStudents=" + str(student_id)
        booking_url = (
            self.base_url + "classes?" + location_query + day_query + student_query
        )

        # Proceed to booking page and find matching class by time
        self.page.goto(booking_url)
        self.page.wait_for_load_state("networkidle")

        link_selector = f"a:has-text('at {timestr}')"
        locator = self.page.locator(link_selector)
        count = locator.count()
        if count > 0:
            # Use negative index if next_week
            idx = -1 if ("Next Week" in daystr or next_week) else 0
            if idx < 0:
                idx = count + idx
            if 0 <= idx < count:
                locator.nth(idx).click()

        self.click(selector="button", filterstr="Enroll Now")
        self.click(selector="button", filterstr="Add to Cart")

    def add_enrollments(
        self, schedule: list = [dict], student_id: int = 0, next_week: bool = False
    ) -> None:
        for this_class in schedule:
            print("\nProcessing class %s..." % str(this_class), end="")

            try:
                self.enroll(
                    location=this_class["Location"],
                    timestr=this_class["Time"],
                    daystr=this_class["Day"],
                    student_id=student_id,
                    next_week=next_week,
                )
                print("success")
            except Exception as e:
                print("error adding this class - error was '%s'" % str(e))

    def process_cart(self, promo_code: str = "") -> None:
        # Navigate to cart
        print("\nProcessing cart")
        self.page.goto(self.base_url + "cart")
        self.page.wait_for_load_state("networkidle")

        # Fill promo code
        if promo_code:
            try:
                self.page.click("text=Use Promo Code")
                self.send_keys(
                    key=promo_code, enter=True, selector="[name='promoCode']"
                )
            except Exception as e:
                print("Error filling promo code - error was '%s'" % str(e))

        # Complete the transaction
        print("\nFinishing processing; clicking Complete Transaction...")
        self.click(selector="button", filterstr="Complete Transaction")
        self.page.wait_for_load_state("networkidle")
        self.browser.close()
        self.playwright.stop()


def main(args: argparse.Namespace) -> None:
    c = iClassPro(base_url=args.base_url)

    try:
        # Read schedule
        schedule = json.load(open(args.schedule, "r"))
    except Exception as e:
        print("Error reading schedule file - error was '%s'" % str(e))
        exit(1)

    try:
        # Get webdriver
        c.webdriver()
        print("Browser launched successfully in headless mode")

        # Login
        print("Attempting login...")
        c.login(
            location=args.location,
            email=args.email,
            password=args.password,
        )
        print("Login completed")

        # Add enrollments
        c.add_enrollments(
            schedule=schedule,
            student_id=args.student_id,
            next_week=args.next_week,
        )

        # Process cart
        c.process_cart(promo_code=args.promo_code)
        print("All operations completed successfully")

    except Exception as e:
        print("Error during execution: %s" % str(e))
        exit(1)
    finally:
        # Ensure browser is closed
        if c.browser:
            try:
                c.browser.close()
                print("Browser closed")
            except:
                pass
        if c.playwright:
            try:
                c.playwright.stop()
                print("Playwright stopped")
            except:
                pass


if __name__ == "__main__":
    # Args set up
    parser = argparse.ArgumentParser(
        description="Add Enrollments for iClassPro Classes",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="https://app.iclasspro.com/portal/scaq/",
        help="iClassPro portal base URL",
    )
    parser.add_argument(
        "--location",
        type=str,
        default="SCAQ",
        help="Portal location (a popup that may appear during the login process)",
    )
    parser.add_argument(
        "--schedule",
        type=str,
        default="schedules/schedule.json",
        help="Schedule - a JSON file with keys Location, Time, and Day",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=os.getenv("ICLASS_EMAIL", ""),
        help="iClassPro Email",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=os.getenv("ICLASS_PASSWORD", ""),
        help="iClassPro Password",
    )
    parser.add_argument(
        "--student-id",
        type=int,
        default=int(os.getenv("ICLASS_STUDENT_ID", "0")),
        help="iClassPro Student ID",
    )
    parser.add_argument(
        "--promo-code",
        type=str,
        default=os.getenv("ICLASS_PROMO_CODE", ""),
        help="iClassPro Promo Code",
    )
    parser.add_argument(
        "--next-week",
        action="store_true",
        help="Apply schedule to next week, not current week",
    )
    args = parser.parse_args()
    main(args)
