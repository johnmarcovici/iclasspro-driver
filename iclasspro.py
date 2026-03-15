#!/usr/bin/env python3

import argparse
import json
import os
import re
import time

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
        # Make the browser look more like a real user browser to reduce automated detection
        headless = os.getenv("ICLASS_HEADLESS", "1").lower() not in ("0", "false", "no")
        slow_mo = int(os.getenv("ICLASS_SLOW_MO", "0"))
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
        )
        self.context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
        # Hide webdriver flag
        self.context.add_init_script(
            """
            // Evasions for common bot detectors
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = { runtime: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) =>
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters);
            """
        )
        self.page = self.context.new_page()

        # Log browser console and page errors for debugging
        self.page.on("console", lambda msg: print(f"PAGE LOG ({msg.type}): {msg.text}"))
        self.page.on("pageerror", lambda err: print(f"PAGE ERROR: {err}"))

        # Track cart-related network activity to verify add-to-cart succeeded
        self._last_cart_response = None

        def _log_request(request):
            url = request.url
            # Log any request that looks like it might be related to cart/promo/booking
            if (
                "cart" in url.lower()
                or "promo" in url.lower()
                or "booking" in url.lower()
                or request.method.upper() == "POST"
                or "api" in url.lower()
            ):
                print(f"NETWORK REQUEST: {request.method} {url}")

        def _log_request_failed(request):
            try:
                print(
                    f"NETWORK REQUEST FAILED: {request.method} {request.url} - {request.failure}"
                )
            except Exception:
                print(f"NETWORK REQUEST FAILED: {request.url}")

        def _log_response(response):
            url = response.url
            if "cart" in url.lower() or "promo" in url.lower() or "api" in url.lower():
                try:
                    body = response.text()[:2000]
                except Exception:
                    body = "<response body unavailable>"

                body_snippet = body.replace("\n", " ")
                print(
                    f"NETWORK RESPONSE: {response.status} {url} len={len(body)} body={body_snippet}"
                )
                self._last_cart_response = response

                if response.status >= 400:
                    print(
                        f"NETWORK ERROR RESPONSE ({response.status}) for {url}: {body_snippet}"
                    )

        self.page.on("request", _log_request)
        self.page.on("requestfailed", _log_request_failed)
        self.page.on("response", _log_response)

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

        if not element:
            print(
                f"CLICK: element not found (selector={selector!r} filter={filterstr!r} kwargs={kwargs})"
            )
            return

        try:
            element.click()
        except Exception as e:
            print(
                f"CLICK: failed for selector={selector!r} filter={filterstr!r} error={e}"
            )
            raise

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

    def _get_cart_item_count_from_dom(self) -> int:
        """Try to detect cart item count from the live DOM."""
        try:
            # Common cart item selectors used in e-commerce UIs
            selectors = [
                "[data-test*='cart-item']",
                "[data-test*='cartItem']",
                ".cart-item",
                ".cartItem",
                ".cart__item",
                ".cart-line-item",
                ".cart-row",
                ".cart-item-row",
                ".cartLineItem",
                "[role='listitem']",
            ]
            for sel in selectors:
                count = self.page.locator(sel).count()
                if count > 0:
                    return count

            # Fallback: look for a text prefix like "X items" in the visible page text.
            body_text = self.page.inner_text("body")
            match = re.search(r"(\d+)\s+(?:items|item)\b", body_text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return 0

    def _get_cart_item_count_from_last_response(self) -> int:
        """Try to infer cart item count from the most recent cart-related HTTP response."""
        if not getattr(self, "_last_cart_response", None):
            return 0

        try:
            text = self._last_cart_response.text()
            if not text:
                return 0

            # If the response is JSON, parse it.
            try:
                data = self._last_cart_response.json()
            except Exception:
                data = None

            if isinstance(data, dict):
                # Recursively search for common keys inside nested objects
                def _find_count(item):
                    if isinstance(item, dict):
                        if "count" in item and isinstance(item["count"], int):
                            return item["count"]
                        for key in ["items", "cartItems", "cart_items", "lineItems"]:
                            if key in item and isinstance(item[key], list):
                                return len(item[key])
                        for v in item.values():
                            found = _find_count(v)
                            if isinstance(found, int):
                                return found
                    elif isinstance(item, list):
                        for element in item:
                            found = _find_count(element)
                            if isinstance(found, int):
                                return found
                    return None

                found_count = _find_count(data)
                if isinstance(found_count, int):
                    return found_count

            # If JSON parsing didn't work, try to regex for something like "items": [ ... ]
            match = re.search(r"\bitems\b\s*:\s*\[", text)
            if match:
                # crude count by counting object starts
                return text.count("{")
        except Exception:
            pass
        return 0

    def _get_cart_item_count(self) -> int:
        """Return an estimated number of cart items.

        It uses both the DOM and the latest cart-related network response to try to detect
        whether there is at least one item in the cart.
        """
        dom_count = self._get_cart_item_count_from_dom()
        if dom_count > 0:
            return dom_count
        return self._get_cart_item_count_from_last_response()

    def _wait_for_cart_item_count(
        self, min_count: int = 1, timeout: int = 20000
    ) -> int:
        """Wait until the cart appears to have at least `min_count` items.

        Returns the detected count, or 0 if it could not be confirmed.
        """
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            count = self._get_cart_item_count()
            if count >= min_count:
                return count
            time.sleep(0.5)
        return 0

    def login(self, location: str = "", email: str = "", password: str = "") -> None:
        # Navigate to login. Some portals require an explicit query parameter to show
        # the login UI (instead of the "Are you a current customer?" prompt).
        login_url = self.base_url.rstrip("/") + "/login?showLogin=1"
        self.page.goto(login_url)
        self.page.wait_for_load_state("networkidle")

        # Some portals first ask: "Are you a current customer?" and show a modal with Yes/No.
        # Click the "Yes" button if it appears before the login form.
        try:
            self.page.wait_for_selector("button:has-text('Yes')", timeout=12000)
            self.click(selector="button", filterstr="Yes")
            # Give the UI a moment to update
            self.page.wait_for_timeout(1000)
        except Exception:
            # It's ok if this step is not present or already moved on
            pass

        # After clicking Yes, select the location if prompted
        try:
            self.page.wait_for_selector("button:has-text('SCAQ')", timeout=8000)
            self.click(selector="button", filterstr="SCAQ")
            # Give the UI a moment to update
            self.page.wait_for_timeout(1000)
        except Exception:
            pass

        # If clicking Yes didn't produce a login form, click the explicit "Log In" link
        # (it typically points to ?showLogin=1).
        try:
            if self.page.locator("input[type='password']").count() == 0:
                login_link = self.page.locator("a[href*='showLogin=1']")
                if login_link.count() > 0:
                    login_link.first.click()
                    self.page.wait_for_load_state("networkidle")
        except Exception:
            pass

        # Wait for the login form to appear (password field is a reliable indicator)
        try:
            self.page.wait_for_selector("input[type='password']", timeout=15000)
        except Exception:
            # Continue anyway and rely on selectors below
            pass

        # Fill credentials and submit.
        # Prefer explicit email/password inputs if available.
        email_elem = self.find_element("input[type='email']") or self.find_element(
            "input[name='email']"
        )
        password_elem = self.find_element(
            "input[type='password']"
        ) or self.find_element("input[name='password']")

        if email_elem:
            email_elem.fill(email)
        else:
            self.send_keys(key=email, selector="[name='email']")

        if password_elem:
            password_elem.fill(password)
            password_elem.press("Enter")
        else:
            self.send_keys(key=password, enter=True, selector="[name='password']")

        # Some portals require an explicit login button click instead of pressing Enter
        try:
            for btn_text in ["Log In", "Login", "Sign In", "Submit"]:
                loc = self.page.locator(f"button:has-text('{btn_text}')")
                if loc.count() > 0:
                    loc.first.click()
                    break
        except Exception:
            pass

    def enroll(
        self,
        location: str = "",
        timestr: str = "",
        daystr: str = "",
        student_id: int = 0,
        promo_code: str = "",
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
        self.page.wait_for_timeout(1500)

        # Save screenshot for debugging
        self.page.screenshot(path="classes_page.png", full_page=True)
        print(f"Classes page URL: {self.page.url}")
        try:
            print(f"Classes page title: {self.page.title()}")
        except Exception:
            pass

        # Save HTML for debugging
        html = self.page.content()
        with open("classes_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Saved classes_page.html for inspection")

        link_selector = f"a:has-text('at {timestr}')"
        locator = self.page.locator(link_selector)
        count = locator.count()
        print(f"Found {count} links with 'at {timestr}'")
        if count > 0:
            # Use negative index if next_week
            idx = -1 if ("Next Week" in daystr or next_week) else 0
            if idx < 0:
                idx = count + idx
            if 0 <= idx < count:
                print(f"Clicking link {idx} for {timestr}")
                locator.nth(idx).click()
        else:
            # Normalize time format to match page display
            normalized_time = timestr.replace("am", " AM").replace("pm", " PM")

            # Try alternative selectors with normalized time
            alt_selectors = [
                f"a:has-text('at {normalized_time}')",
                f"button:has-text('at {normalized_time}')",
                f"div:has-text('at {normalized_time}')",
                f"span:has-text('at {normalized_time}')",
                f"a:has-text('{normalized_time}')",
                f"button:has-text('{normalized_time}')",
                f"div:has-text('{normalized_time}')",
                f"span:has-text('{normalized_time}')",
                f"h2:has-text('at {timestr}')",
                f"h2:has-text('at {normalized_time}')",
            ]
            for sel in alt_selectors:
                alt_locator = self.page.locator(sel)
                alt_count = alt_locator.count()
                print(f"Alternative selector '{sel}' found {alt_count} elements")
                if alt_count > 0:
                    print(f"Clicking alternative selector '{sel}' for {timestr}")
                    # If it's h2, find the ancestor a
                    if "h2:" in sel:
                        alt_locator.first.locator("xpath=ancestor::a").first.click()
                    else:
                        alt_locator.first.click()
                    break

        # Wait for enrollment workflow to load
        try:
            self.page.wait_for_selector("button:has-text('Enroll Now')", timeout=10000)
        except Exception:
            pass

        self.click(selector="button", filterstr="Enroll Now")
        self.page.wait_for_timeout(1500)

        # Wait for the Add to Cart button to become available
        try:
            self.page.wait_for_selector("button:has-text('Add to Cart')", timeout=10000)
        except Exception:
            pass

        # Check for promo code field after Enroll Now
        if promo_code:
            try:
                self.page.wait_for_timeout(1000)  # Wait for page to update
                # Look for promo code input on enrollment page
                promo_selectors = [
                    "[name='promoCode']",
                    "[name='promo_code']",
                    "[name='promocode']",
                    "[placeholder*='promo']",
                    "[placeholder*='code']",
                    "[placeholder*='coupon']",
                    "input[type='text']",
                    "textarea",
                    "[contenteditable]",
                ]
                for selector in promo_selectors:
                    try:
                        elements = self.page.locator(selector)
                        if elements.count() > 0:
                            for i in range(elements.count()):
                                elem = elements.nth(i)
                                placeholder = elem.get_attribute("placeholder") or ""
                                name = elem.get_attribute("name") or ""
                                if (
                                    "promo" in placeholder.lower()
                                    or "code" in placeholder.lower()
                                    or "coupon" in placeholder.lower()
                                    or "promo" in name.lower()
                                ):
                                    elem.fill(promo_code)
                                    elem.press("Enter")
                                    print(
                                        f"Applied promo code during enrollment for {location} {timestr}"
                                    )
                                    break
                            else:
                                continue
                            break
                    except:
                        continue
            except Exception as e:
                print(f"Warning: Could not apply promo code during enrollment - {e}")

        self.click(selector="button", filterstr="Add to Cart")
        self.page.wait_for_timeout(2000)
        self.page.screenshot(path="after_add_to_cart.png", full_page=True)
        print("Saved after_add_to_cart.png")

        # Give the app time to register the cart update before navigating away.
        # In the live UI, clicking too quickly away can abort the add-to-cart workflow.
        try:
            # Wait for at least one cart-related POST/PUT request to complete.
            try:
                self.page.wait_for_response(
                    lambda r: "cart" in r.url.lower()
                    and r.request.method.upper() in ("POST", "PUT", "PATCH"),
                    timeout=15000,
                )
            except Exception:
                # It's ok if we didn't observe the request; we'll fall back to DOM checks below.
                pass

            self.page.wait_for_timeout(1500)
            self.page.wait_for_load_state("networkidle", timeout=10000)

            cart_count = self._wait_for_cart_item_count(min_count=1, timeout=15000)
            if cart_count > 0:
                print(f"Detected {cart_count} item(s) in cart after Add to Cart")
            else:
                # Fallback: look for common toast-like success messages.
                success_locators = [
                    "text=Added to cart",  # common toast message
                    "text=Added!",
                    "text=Item added",
                    "text=Added successfully",
                    "text=Your cart has been updated",
                    "text=Enrollment added",
                    "text=Added to your cart",
                ]
                for sel in success_locators:
                    if self.page.locator(sel).count() > 0:
                        print(f"Detected success message after Add to Cart: {sel}")
                        break
                else:
                    # Unable to confirm add-to-cart succeeded
                    print(
                        "Warning: Could not confirm item was added to cart (no cart count and no success toast)."
                    )
        except Exception as e:
            print(f"Error confirming Add to Cart: {e}")
            raise

    def add_enrollments(
        self,
        schedule: list = [dict],
        student_id: int = 0,
        promo_code: str = "",
        next_week: bool = False,
    ) -> None:
        for this_class in schedule[:1]:
            print("\nProcessing class %s..." % str(this_class), end="")

            try:
                self.enroll(
                    location=this_class["Location"],
                    timestr=this_class["Time"],
                    daystr=this_class["Day"],
                    student_id=student_id,
                    promo_code=promo_code,
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
        print(f"Cart page URL: {self.page.url}")
        try:
            print(f"Cart page title: {self.page.title()}")
        except Exception:
            pass

        # Save cart page screenshot immediately for inspection
        try:
            self.page.screenshot(path="cart_debug.png", full_page=True)
            print("Saved cart_debug.png for inspection")
        except Exception as e:
            print("Warning: could not save cart screenshot:", e)

        # Wait for the cart loading spinner to go away (if any)
        try:
            self.page.wait_for_selector(".loading-icon", state="hidden", timeout=20000)
        except Exception:
            print("Warning: loading spinner did not hide within timeout")

        # If the cart fails to load, close the error modal and retry once
        if self.page.locator("text=Error loading cart items").count() > 0:
            print("Detected 'Error loading cart items' modal; closing and retrying")
            try:
                self.page.locator("button:has-text('Close')").first.click()
            except Exception:
                pass
            self.page.wait_for_timeout(1500)
            self.page.reload(wait_until="networkidle")
            self.page.wait_for_timeout(1500)

        # Verify the cart has at least one item before continuing.
        cart_count = self._wait_for_cart_item_count(min_count=1, timeout=20000)
        if cart_count == 0:
            print(
                "Warning: cart does not contain any items after navigation; aborting cart processing."
            )
            return
        print(f"Cart contains {cart_count} item(s)")

        # Debug: dump a small slice of the page HTML around "promo" to help locate the field
        try:
            html = self.page.content()
            low = html.lower()
            if "promo" in low:
                idx = low.find("promo")
                snippet = html[max(0, idx - 250) : idx + 250]
                print("Debug: HTML snippet around 'promo':")
                print(snippet)
        except Exception:
            pass

        # Fill promo code (and ensure we don't complete transaction without it)
        promo_applied = False
        if promo_code:
            try:
                # Ensure the cart UI is loaded
                try:
                    self.page.wait_for_selector(
                        "text=Complete Transaction", timeout=20000
                    )
                except Exception:
                    print(
                        "Warning: 'Complete Transaction' button not found - cart may still be loading"
                    )

                # Abort if cart appears empty
                if self.page.locator("text=Your cart is empty").count() > 0:
                    print("Cart appears empty; aborting.")
                    return

                # Save cart page dump for debugging promo code UI
                try:
                    html = self.page.content()
                    with open("cart_debug.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    self.page.screenshot(path="cart_debug.png", full_page=True)
                    print("Saved cart_debug.html and cart_debug.png for inspection")
                except Exception as e:
                    print("Warning: could not save cart debug artifacts:", e)

                # Try to click the 'Use Promo Code' trigger (may be hidden inside dynamic UI)
                promo_clicked = False
                for text in [
                    "Use Promo Code",
                    "Use promo code",
                    "Promo Code",
                    "Apply Promo Code",
                ]:
                    try:
                        locator = self.page.get_by_text(text, exact=False)
                        if locator.count() > 0:
                            locator.first.click()
                            promo_clicked = True
                            self.page.wait_for_timeout(1500)
                            print(f"Clicked '{text}'")
                            break
                    except Exception:
                        continue

                # If click did not work, try a brute-force search through all elements for the text.
                if not promo_clicked:
                    try:
                        clicked = self.page.evaluate(
                            "(text) => {\n"
                            "  const walk = (root) => {\n"
                            "    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);\n"
                            "    let node;\n"
                            "    while ((node = walker.nextNode())) {\n"
                            "      try {\n"
                            "        if (node.innerText && node.innerText.toLowerCase().includes(text.toLowerCase())) {\n"
                            "          node.click();\n"
                            "          return true;\n"
                            "        }\n"
                            "      } catch (e) {}\n"
                            "      if (node.shadowRoot) {\n"
                            "        const clicked = walk(node.shadowRoot);\n"
                            "        if (clicked) return true;\n"
                            "      }\n"
                            "    }\n"
                            "    return false;\n"
                            "  };\n"
                            "  return walk(document);\n"
                            "}",
                            "Use Promo Code",
                        )
                        if clicked:
                            promo_clicked = True
                            self.page.wait_for_timeout(1500)
                            print("Clicked 'Use Promo Code' via DOM walk")
                    except Exception:
                        pass

                # Find promo code input
                promo_input = None
                for selector in [
                    "#modal-remove-promo-code input",
                    "#modal-promo-code input",
                    "input[placeholder*='promo']",
                    "input[placeholder*='code']",
                    "input[name*='promo']",
                    "input[name*='code']",
                    "input[id*='promo']",
                    "input[id*='code']",
                ]:
                    try:
                        locator = self.page.locator(selector)
                        if locator.count() > 0 and locator.first.is_visible():
                            promo_input = locator.first
                            break
                    except Exception:
                        continue

                if not promo_input:
                    print("Promo code input not found; aborting transaction")
                    return

                promo_input.fill(promo_code)
                promo_input.press("Enter")
                promo_applied = True
                print(f"Applied promo code on cart page: {promo_code}")
                self.page.wait_for_timeout(2000)

            except Exception as e:
                print("Error filling promo code on cart page - error was '%s'" % str(e))
                return

        # Abort if promo code was required but not applied
        if promo_code and not promo_applied:
            print("Promo code not applied; aborting before completing transaction")
            return

        # Complete the transaction (this might lead to a checkout page)
        print("\nFinishing processing; clicking Complete Transaction...")
        self.click(selector="button", filterstr="Complete Transaction")
        self.page.wait_for_load_state("networkidle")

        # If we have a promo code and didn't apply it yet, try on the checkout page
        if promo_code:
            try:
                print("Checking for promo code input on checkout page...")
                self.page.wait_for_load_state("domcontentloaded")

                # Debug: Print all form-related elements on checkout page
                print("Debug: Available form elements on checkout page:")
                form_elements = [
                    "input",
                    "textarea",
                    "select",
                    "[contenteditable]",
                    "[role='textbox']",
                ]
                total_elements = 0
                for elem_type in form_elements:
                    elements = self.page.locator(elem_type)
                    count = elements.count()
                    total_elements += count
                    print(f"Found {count} {elem_type} elements")
                    for i in range(min(count, 5)):  # Show first 5 of each type
                        try:
                            elem = elements.nth(i)
                            tag_name = elem.evaluate("el => el.tagName.toLowerCase()")
                            input_type = elem.get_attribute("type") or ""
                            name = elem.get_attribute("name") or ""
                            placeholder = elem.get_attribute("placeholder") or ""
                            id_attr = elem.get_attribute("id") or ""
                            class_attr = elem.get_attribute("class") or ""
                            text_content = (
                                elem.text_content()[:50] if elem.text_content() else ""
                            )
                            print(
                                f"  {elem_type} {i}: {tag_name} type='{input_type}' name='{name}' placeholder='{placeholder}' id='{id_attr}' class='{class_attr}' text='{text_content}'"
                            )
                        except Exception as e:
                            print(f"  Error reading {elem_type} {i}: {e}")
                            continue
                print(f"Total form elements found: {total_elements}")

                # Try to find promo code input on checkout page
                promo_input_selectors = [
                    "[name='promoCode']",
                    "[name='promo_code']",
                    "[name='promocode']",
                    "[name='code']",
                    "[placeholder*='promo']",
                    "[placeholder*='Promo']",
                    "[placeholder*='code']",
                    "[placeholder*='Code']",
                    "[placeholder*='coupon']",
                    "[placeholder*='Coupon']",
                    "input[type='text'][class*='promo']",
                    "input[type='text'][id*='promo']",
                    "textarea[class*='promo']",
                    "textarea[id*='promo']",
                    "[contenteditable][class*='promo']",
                    "[contenteditable][id*='promo']",
                    "#promo-code-input",
                    ".promo-code-input",
                    "input[aria-label*='promo']",
                    "input[aria-label*='Promo']",
                    "input[aria-label*='coupon']",
                    "input[aria-label*='Coupon']",
                    "textarea[aria-label*='promo']",
                    "textarea[aria-label*='Promo']",
                    "[contenteditable][aria-label*='promo']",
                    "[contenteditable][aria-label*='Promo']",
                ]

                promo_input = None
                for selector in promo_input_selectors:
                    try:
                        locator = self.page.locator(selector)
                        if locator.count() > 0:
                            first_elem = locator.first
                            if first_elem.is_visible():
                                promo_input = first_elem
                                break
                    except:
                        continue
                    all_text_inputs = self.page.locator(
                        "input[type='text'], textarea, [contenteditable], [role='textbox']"
                    )
                    for i in range(all_text_inputs.count()):
                        input_elem = all_text_inputs.nth(i)
                        try:
                            tag_name = input_elem.evaluate(
                                "el => el.tagName.toLowerCase()"
                            )
                            placeholder = input_elem.get_attribute("placeholder") or ""
                            name = input_elem.get_attribute("name") or ""
                            aria_label = input_elem.get_attribute("aria-label") or ""
                            class_attr = input_elem.get_attribute("class") or ""
                            id_attr = input_elem.get_attribute("id") or ""
                            text_content = (
                                input_elem.text_content()[:30]
                                if input_elem.text_content()
                                else ""
                            )

                            # Check if this looks like a promo/coupon field
                            is_promo_field = (
                                "promo" in placeholder.lower()
                                or "code" in placeholder.lower()
                                or "coupon" in placeholder.lower()
                                or "promo" in name.lower()
                                or "code" in name.lower()
                                or "coupon" in name.lower()
                                or "promo" in aria_label.lower()
                                or "code" in aria_label.lower()
                                or "coupon" in aria_label.lower()
                                or "promo" in class_attr.lower()
                                or "code" in class_attr.lower()
                                or "coupon" in class_attr.lower()
                                or "promo" in id_attr.lower()
                                or "code" in id_attr.lower()
                                or "coupon" in id_attr.lower()
                            )

                            if is_promo_field:
                                # Try to fill it
                                if tag_name in ["input", "textarea"]:
                                    input_elem.fill(promo_code)
                                elif (
                                    input_elem.get_attribute("contenteditable")
                                    == "true"
                                ):
                                    input_elem.clear()
                                    input_elem.type(promo_code)
                                else:
                                    # Try click and type
                                    input_elem.click()
                                    self.page.keyboard.type(promo_code)

                                input_elem.press("Enter")
                                print(
                                    f"Applied promo code to {tag_name} element with placeholder '{placeholder}' name '{name}' aria-label '{aria_label}' class '{class_attr}' id '{id_attr}'"
                                )
                                self.page.wait_for_timeout(2000)
                                break
                        except:
                            continue
                    else:
                        print(
                            "Error: Could not find promo code input on checkout page either"
                        )

            except Exception as e:
                print(
                    "Error filling promo code on checkout page - error was '%s'"
                    % str(e)
                )

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
            promo_code=args.promo_code,
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
