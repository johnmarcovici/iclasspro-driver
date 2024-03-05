import argparse
import json

from time import sleep
from schedule_builder import main as schedule_builder

# Selenium Imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.remote.webelement import WebElement


class iClassPro:
    def __init__(self, base_url: str = ""):
        self.base_url = base_url
        self.driver = None

    def webdriver(self, chrome_driver: str = "", chrome_binary: str = "") -> None:
        options = webdriver.ChromeOptions()
        options.add_argument("start-maximized")
        options.binary_location = chrome_binary
        self.driver = webdriver.Chrome(service=Service(chrome_driver), options=options)
        self.driver.maximize_window()

    def find_element(
        self,
        by: str = By.TAG_NAME,
        searchstr: str = "button",
        filterstr: str = "",
        index: int = 0,
        maxtries: int = 5,
    ) -> WebElement:
        elements = []
        numtries = 0
        while len(elements) == 0 and numtries < maxtries:
            elements = [
                element
                for element in self.driver.find_elements(by, searchstr)
                if filterstr in element.text
            ]
            numtries += 1
            sleep(1)
        return elements[index]

    def click(self, element: WebElement = None, **kwargs) -> None:
        if element is None:
            element = self.find_element(**kwargs)

        self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
        sleep(1)
        element.click()
        sleep(3)

    def send_keys(
        self, element: WebElement = None, key: str = "", enter: bool = False, **kwargs
    ) -> None:
        if element is None:
            element = self.find_element(**kwargs)

        self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
        sleep(1)
        element.send_keys(key)
        if enter:
            element.send_keys(Keys.ENTER)
        sleep(3)

    def login(self, location: str = "", email: str = "", password: str = "") -> None:
        # Navigate to login
        self.driver.get(self.base_url + "login")
        sleep(3)

        try:
            # A location prompt may pop up
            self.click(by=By.TAG_NAME, searchstr="button", filterstr=location)
        except:
            pass

        # Process the login page: click Yes for account, then enter username and password
        self.click(by=By.TAG_NAME, searchstr="button", filterstr="Yes")
        self.send_keys(key=email, by=By.NAME, searchstr="email")
        self.send_keys(key=password, enter=True, by=By.NAME, searchstr="password")

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
        self.driver.get(booking_url)
        sleep(5)
        self.click(
            by=By.PARTIAL_LINK_TEXT,
            searchstr="at " + timestr,
            index=-(("Next Week" in daystr) or next_week),
        )
        self.click(by=By.TAG_NAME, searchstr="button", filterstr="Enroll Now")
        self.click(by=By.TAG_NAME, searchstr="button", filterstr="Add to Cart")
        sleep(10)

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
        self.driver.get(self.base_url + "cart")
        sleep(5)

        # Fill promo code
        self.click(by=By.PARTIAL_LINK_TEXT, searchstr="Use Promo Code")
        self.send_keys(key=promo_code, enter=True, by=By.NAME, searchstr="promoCode")

        # Complete the transaction
        nsec_wait = 10
        print(
            "\nFinished Processing - review the schedule and the process will accept all in %d seconds."
            % nsec_wait
        )
        sleep(nsec_wait)
        self.click(by=By.TAG_NAME, searchstr="button", filterstr="Complete Transaction")
        sleep(15)
        self.driver.quit()


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
        default="schedule.json",
        help="Schedule - a JSON file with keys Location, Time, and Day",
    )
    parser.add_argument("--email", type=str, default="", help="iClassPro Email")
    parser.add_argument("--password", type=str, default="", help="iClassPro Password")
    parser.add_argument(
        "--student-id", type=int, default=0, help="iClassPro Student ID"
    )
    parser.add_argument(
        "--promo-code", type=str, default="", help="iClassPro Promo Code"
    )
    parser.add_argument(
        "--chrome-driver",
        type=str,
        default="./chromedriver",
        help="Path to chrome driver",
    )
    parser.add_argument(
        "--chrome-binary",
        type=str,
        default="/usr/bin/google-chrome",
        help="Path to chrome binary",
    )
    parser.add_argument(
        "--next-week",
        action="store_true",
        help="Apply schedule to next week, not current week",
    )
    parser.add_argument(
        "--build-schedule", action="store_true", help="Define schedule via GUI"
    )
    args = parser.parse_args()

    # Build schedule
    if args.build_schedule:
        schedule_builder(schedule=args.schedule)

    c = iClassPro(base_url=args.base_url)

    try:
        # Read schedule
        schedule = json.load(open(args.schedule, "r"))
    except Exception as e:
        print("Error reading schedule file - error was '%s'" % str(e))
        exit()

    # Get webdriver
    c.webdriver(chrome_driver=args.chrome_driver, chrome_binary=args.chrome_binary)

    # Login
    c.login(
        location=args.location,
        email=args.email,
        password=args.password,
    )

    # Add enrollments
    c.add_enrollments(
        schedule=schedule,
        student_id=args.student_id,
        next_week=args.next_week,
    )

    # Process cart
    c.process_cart(promo_code=args.promo_code)
