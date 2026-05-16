#!/usr/bin/env python3
"""JWT REST client for iClassPro portal enrollment (cart + checkout).

Reverse-engineered from the customer portal SPA (``/api/jwt/v1`` on
``app.iclasspro.com``). Discovery still uses the public Open API in
``iclasspro.py``; this module handles authenticated enrollment only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import Any, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

JWT_API_BASE = "https://app.iclasspro.com/api/jwt/v1"
LOGIN_URL = f"{JWT_API_BASE}/login"
OPEN_API_BASE = "https://app.iclasspro.com/api/open/v1"

_CLASS_ID_RE = re.compile(r"/class-details/(\d+)")


class EnrollmentSkipped(Exception):
    """Intentional skip (already enrolled / in cart)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class IClassProAPIError(RuntimeError):
    """API returned hasErrors or an unexpected HTTP status."""


def _api_message(body: Any, fallback: str = "API error") -> str:
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or fallback)
    return fallback


def _unwrap_data(body: Any) -> Any:
    """Return ``data`` from a portal envelope; raise on ``hasErrors``."""
    if not isinstance(body, dict):
        return body
    if body.get("hasErrors"):
        errors = body.get("errors")
        data_err = (body.get("data") or {}).get("errors")
        detail = errors or data_err or body
        raise IClassProAPIError(f"{_api_message(body)}: {detail}")
    if "data" in body:
        return body["data"]
    return body


class IClassProAPIClient:
    """Customer-portal JWT API (token query param, SPA-shaped payloads)."""

    def __init__(self, email: str, password: str, portal: str) -> None:
        self.email = email
        self.password = password
        self.portal = portal.strip().strip("/")
        self.token: Optional[str] = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": "https://portal.iclasspro.com",
                "Referer": f"https://portal.iclasspro.com/{self.portal}/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            }
        )
        self._location_ids: dict[str, int] = {}

    def login(self) -> str:
        """Authenticate; store ``access_token`` for subsequent calls."""
        logger.info("Logging in as %s (account=%s)...", self.email, self.portal)
        payload = {
            "email": self.email,
            "password": self.password,
            "type": "customer",
            "account": self.portal,
            "multipleLoginSupport": True,
        }
        resp = self.session.post(LOGIN_URL, json=payload, timeout=45)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = _api_message(body, resp.text)
            except ValueError:
                msg = resp.text or resp.reason
            hint = ""
            if resp.status_code == 500 and "account" in payload:
                hint = (
                    " (If credentials work in the browser, prefer "
                    "ICLASS_ENROLLMENT_DRIVER=playwright or hybrid.)"
                )
            raise RuntimeError(f"Login failed ({resp.status_code}): {msg}{hint}") from None

        body = resp.json()
        if isinstance(body, dict) and body.get("hasErrors"):
            raise RuntimeError(f"Login failed: {_api_message(body)}")

        data = _unwrap_data(body)
        token = None
        if isinstance(data, dict):
            token = data.get("access_token") or data.get("token")
        if not token and isinstance(body, dict):
            token = body.get("access_token") or body.get("token")
        if not token:
            raise RuntimeError(f"Login response contained no access_token: {body}")
        self.token = str(token)
        logger.info("Login successful.")
        return self.token

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        if not self.token:
            self.login()
        p = dict(params or {})
        p["token"] = self.token
        path = path if path.startswith("/") else f"/{path}"
        url = f"{JWT_API_BASE}{path}"
        resp = self.session.request(
            method, url, json=json_body, params=p, timeout=45
        )
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
                msg = _api_message(err_body, resp.text)
            except ValueError:
                msg = resp.text or resp.reason
            raise IClassProAPIError(f"HTTP {resp.status_code}: {msg}")
        body = resp.json()
        return _unwrap_data(body)

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, payload: dict, params: Optional[dict] = None) -> Any:
        return self._request("POST", path, json_body=payload, params=params)

    def resolve_location_id(self, location_name: str) -> int:
        """Map a schedule location label to Open API location id."""
        key = location_name.strip().lower()
        if key in self._location_ids:
            return self._location_ids[key]
        data = self._fetch_open(f"{self.portal}/locations")
        rows = data if isinstance(data, list) else (data or {}).get("data") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            lid = row.get("id")
            if name and lid is not None:
                self._location_ids[name.lower()] = int(lid)
        needle = key
        for name, lid in self._location_ids.items():
            if needle == name or needle in name or name in needle:
                return lid
        raise ValueError(f"Could not resolve location id for {location_name!r}")

    def _fetch_open(self, path: str, params: Optional[dict] = None) -> Any:
        slug_path = path.strip("/")
        url = f"{OPEN_API_BASE}/{slug_path}"
        resp = self.session.get(url, params=params or {}, timeout=45)
        resp.raise_for_status()
        return resp.json()

    def fetch_open_class_row(
        self,
        class_id: int,
        *,
        day_index: Optional[int] = None,
        location_filter: Optional[str] = None,
    ) -> Optional[dict]:
        """Find one class row from the public catalog by id (and optional filters)."""
        page = 1
        while page <= 50:
            params: dict[str, Any] = {"limit": 80, "page": page}
            if day_index is not None:
                params["days"] = str(day_index)
            if location_filter:
                params["q"] = location_filter
            body = self._fetch_open(f"{self.portal}/classes", params)
            rows = body.get("data") or []
            for row in rows:
                if int(row.get("id") or 0) == int(class_id):
                    return row
            total = int(body.get("totalRecords") or 0)
            if not rows or page * 80 >= total:
                break
            page += 1
        return None

    def get_class_detail(self, class_id: int, student_id: int) -> dict:
        data = self._get(
            f"classes/{class_id}", params={"selectedStudentIds": student_id}
        )
        return data if isinstance(data, dict) else {}

    def get_payment_methods(self) -> list:
        data = self._get("family-payment-method")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("paymentMethods") or data.get("methods") or []
        return []

    def fetch_new_cart_item(
        self,
        class_id: int,
        session_id: int,
        student_id: int,
        *,
        start_date: Optional[str] = None,
    ) -> dict:
        path = f"/new-cart-item/{class_id}/{session_id}"
        params: dict[str, Any] = {"selectedStudentIds": student_id}
        if start_date:
            params["startDate"] = start_date
        data = self._get(path, params=params)
        return data if isinstance(data, dict) else {}

    def _build_cart_item_payload(
        self,
        new_cart_data: dict,
        class_id: int,
        session_id: int,
        student_id: int,
        *,
        start_date: Optional[str] = None,
    ) -> dict:
        """Build a ``newCartItems[]`` element from ``new-cart-item`` API data."""
        item = dict(new_cart_data.get("cartItem") or new_cart_data.get("newCartItem") or {})
        if not item:
            details = new_cart_data.get("cartItemDetails") or {}
            if isinstance(details, dict) and details:
                item = {"cartItemDetails": details}
        item.setdefault("objectId", class_id)
        item.setdefault("sessionId", session_id)
        item.setdefault("selectedStudentIds", [student_id])
        if start_date:
            item.setdefault("startDate", start_date)
        elif new_cart_data.get("startDate"):
            item.setdefault("startDate", new_cart_data.get("startDate"))
        return item

    def add_session_to_cart(
        self,
        class_id: int,
        session_id: int,
        student_id: int,
        location_id: int,
        *,
        start_date: Optional[str] = None,
    ) -> None:
        """Validate and add one session — mirrors portal Enroll → Add to Cart."""
        logger.info(
            "Adding to cart: class=%s session=%s student=%s location=%s",
            class_id,
            session_id,
            student_id,
            location_id,
        )
        draft = self.fetch_new_cart_item(
            class_id, session_id, student_id, start_date=start_date
        )
        cart_item = self._build_cart_item_payload(
            draft, class_id, session_id, student_id, start_date=start_date
        )
        new_cart_items = [cart_item]
        body = {"newCartItems": new_cart_items, "locationId": location_id}
        self._post("/validate-cart-item", body)
        self._post("/add-cart-item", body)
        logger.info("Cart item added (class=%s, session=%s).", class_id, session_id)

    def apply_promo_code(self, code: str) -> None:
        logger.info("Applying promo code: %s", code)
        self._post("/add-promo-code", {"promoCode": code, "promoCodes": [code]})

    def validate_cart(self, location_id: int) -> dict:
        data = self._get(f"/validate-cart/{location_id}")
        return data if isinstance(data, dict) else {}

    def checkout(
        self,
        location_id: int,
        *,
        payment_method_id: Optional[int] = None,
        complete_transaction: bool = True,
    ) -> dict:
        """Submit cart for payment at a location (``process-cart``)."""
        if not complete_transaction:
            logger.info("Dry-run: skipping process-cart (complete_transaction=False).")
            return {
                "dry_run": True,
                "message": "Transaction not submitted (dry-run mode).",
            }

        cart_state = self.validate_cart(location_id)
        transaction_id = cart_state.get("transactionId") or cart_state.get(
            "TransactionId"
        )
        payment_total = cart_state.get("paymentTotal") or cart_state.get("total") or 0
        payment_amount = cart_state.get("paymentAmount") or payment_total

        if payment_method_id is None:
            methods = self.get_payment_methods()
            if methods:
                payment_method_id = methods[0].get("id")

        payload: dict[str, Any] = {
            "useCardOnFile": True,
            "paymentAmount": payment_amount,
            "paymentTotal": payment_total,
            "useAccountCredit": False,
        }
        if transaction_id is not None:
            payload["transactionId"] = transaction_id
            payload["TransactionId"] = transaction_id
        if payment_method_id is not None:
            payload["paymentMethodId"] = payment_method_id

        logger.info(
            "Submitting process-cart (location=%s, transactionId=%s)...",
            location_id,
            transaction_id,
        )
        result = self._post(f"/process-cart/{location_id}", payload)
        return result if isinstance(result, dict) else {"result": result}


def _parse_class_id_from_url(url: str) -> Optional[int]:
    m = _CLASS_ID_RE.search(url or "")
    return int(m.group(1)) if m else None


def _session_id_from_open_row(row: dict) -> Optional[int]:
    sessions = row.get("sessions") or []
    if not sessions:
        return None
    first = sessions[0]
    if isinstance(first, dict):
        sid = first.get("id") or first.get("sessionId")
    else:
        sid = first
    try:
        return int(sid) if sid is not None else None
    except (TypeError, ValueError):
        return None


def enroll_from_schedule(
    client: IClassProAPIClient,
    schedule: list,
    student_id: int,
    *,
    promo_code: Optional[str] = None,
    complete_transaction: bool = False,
) -> tuple[list, Optional[dict]]:
    """Add each schedule row to the cart, then checkout once for its location."""
    from iclasspro import DAY_TO_QUERY_INDEX

    summary: list = []
    added_by_location: dict[int, int] = {}

    for entry in schedule:
        location = (entry.get("Location") or entry.get("location") or "").strip()
        time_str = (entry.get("Time") or entry.get("time") or "").strip()
        day = (entry.get("Day") or entry.get("day") or "").strip()
        label = f"{location} {day} {time_str}".strip()
        result: dict[str, Any] = {
            "label": label,
            "location": location,
            "time": time_str,
            "day": day,
        }

        try:
            location_id = client.resolve_location_id(location)
            class_url = str(entry.get("url") or "").strip()
            class_id = _parse_class_id_from_url(class_url)

            day_idx = DAY_TO_QUERY_INDEX.get(day.lower())
            open_row = None
            if class_id is not None:
                open_row = client.fetch_open_class_row(
                    class_id, day_index=day_idx, location_filter=location
                )
            elif day_idx is not None:
                open_row = _match_open_catalog_row(client, location, time_str, day_idx)
                if open_row:
                    class_id = int(open_row["id"])

            if class_id is None:
                raise ValueError(f"No class found for {label}")

            session_id = None
            start_date = None
            if open_row:
                session_id = _session_id_from_open_row(open_row)
                start_date = open_row.get("startDate") or None
                if not start_date:
                    dates = open_row.get("availableDates") or []
                    if dates:
                        start_date = dates[0]

            if session_id is None:
                detail = client.get_class_detail(class_id, student_id)
                sessions = detail.get("sessions") or []
                if not sessions:
                    raise ValueError(f"No sessions found for class {class_id}")
                sess = sessions[0]
                session_id = int(sess.get("id") or sess.get("sessionId"))
                start_date = start_date or sess.get("startDate")

            client.add_session_to_cart(
                class_id,
                session_id,
                student_id,
                location_id,
                start_date=start_date,
            )
            added_by_location[location_id] = added_by_location.get(location_id, 0) + 1
            result["status"] = "Success"
            result["details"] = "Added to cart"

        except EnrollmentSkipped as exc:
            result["status"] = "Skipped"
            result["details"] = exc.reason
        except Exception as exc:
            logger.error("Failed for %s: %s", label, exc)
            result["status"] = "Failed"
            result["details"] = str(exc)

        summary.append(result)

    checkout_result: Optional[dict] = None
    if added_by_location and complete_transaction:
        if promo_code:
            try:
                client.apply_promo_code(promo_code)
            except Exception as exc:
                logger.warning("Promo code failed (continuing): %s", exc)

        # One checkout per location that received items (usually one).
        checkout_results = []
        for loc_id in added_by_location:
            checkout_results.append(client.checkout(loc_id, complete_transaction=True))
        checkout_result = (
            checkout_results[0] if len(checkout_results) == 1 else checkout_results
        )

    elif added_by_location and not complete_transaction:
        loc_id = next(iter(added_by_location))
        checkout_result = client.checkout(loc_id, complete_transaction=False)

    return summary, checkout_result


def _match_open_catalog_row(
    client: IClassProAPIClient,
    location: str,
    time_str: str,
    day_index: int,
) -> Optional[dict]:
    """Match schedule row against Open API catalog (same shape as discovery)."""
    from iclasspro import _normalize_open_time, _open_api_fetch_classes_all

    rows = _open_api_fetch_classes_all(client.portal)
    loc_l = location.lower()
    want_time = _normalize_open_time(time_str)
    for row in rows:
        sched_list = row.get("schedule") or []
        if not sched_list:
            continue
        sched = sched_list[0]
        day_num = int(sched.get("dayNumber") or 0)
        if day_num != day_index:
            continue
        row_time = _normalize_open_time(str(sched.get("startTime") or ""))
        name = (row.get("name") or "").lower()
        if want_time != row_time:
            continue
        if loc_l not in name:
            continue
        return row
    return None


def run_api_enrollment(args: argparse.Namespace) -> int:
    """JWT enrollment entry (called from ``iclasspro.py --driver api``)."""
    if not args.email or not args.password:
        logger.error("--email and --password required for API enrollment.")
        return 1
    if not args.schedule:
        logger.error("--schedule required for enrollment.")
        return 1

    portal_slug = args.portal or _default_portal_slug()
    try:
        with open(args.schedule, encoding="utf-8") as fh:
            schedule = json.load(fh)
    except OSError as exc:
        logger.error("Could not read schedule %r: %s", args.schedule, exc)
        return 1

    if not schedule:
        logger.error("Schedule is empty.")
        return 1

    client = IClassProAPIClient(args.email, args.password, portal_slug)
    try:
        client.login()
    except Exception as exc:
        logger.error("Login failed: %s", exc)
        logger.error(
            "Use Playwright for enrollment if JWT login fails: "
            "ICLASS_ENROLLMENT_DRIVER=playwright in .env or the dashboard driver dropdown."
        )
        return 1

    effective_complete = args.complete_transaction and not args.dry_run
    if args.dry_run and args.complete_transaction:
        logger.info("Dry-run flag enabled; overriding complete-transaction.")

    logger.info(
        "API enrollment: %d class(es), complete_transaction=%s",
        len(schedule),
        effective_complete,
    )

    summary, checkout_result = enroll_from_schedule(
        client,
        schedule,
        int(args.student_id),
        promo_code=args.promo_code or None,
        complete_transaction=effective_complete,
    )

    print("\n=== Enrollment Run Report ===")
    for row in summary:
        icon = {"Success": "\u2714", "Skipped": "\u27f3", "Failed": "\u2718"}.get(
            row.get("status"), "?"
        )
        print(f"  {icon} [{row.get('status', '?'):8s}] {row.get('label', '')}")
        if row.get("details") and row.get("status") != "Success":
            print(f"             {row['details']}")

    if checkout_result is not None:
        print("\n=== Checkout ===")
        print(json.dumps(checkout_result, indent=2, default=str)[:2000])

    failed = sum(1 for r in summary if r.get("status") == "Failed")
    print(
        f"\nSummary: "
        f"{sum(1 for r in summary if r.get('status') == 'Success')} added, "
        f"{sum(1 for r in summary if r.get('status') == 'Skipped')} skipped, "
        f"{failed} failed."
    )
    return 0 if failed == 0 else 1


def _default_portal_slug() -> str:
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


def main() -> int:
    """CLI probe: ``python -m iclasspro_jwt --email ... --password ...``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    from dotenv import load_dotenv

    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="iClassPro JWT API probe")
    parser.add_argument("--email", default=os.getenv("ICLASS_EMAIL"))
    parser.add_argument("--password", default=os.getenv("ICLASS_PASSWORD"))
    parser.add_argument("--portal", default=os.getenv("ICLASS_PORTAL") or _default_portal_slug())
    parser.add_argument("--student-id", type=int, default=os.getenv("ICLASS_STUDENT_ID"))
    parser.add_argument("--schedule", default=os.getenv("ICLASS_SCHEDULE"))
    parser.add_argument("--promo-code", default=os.getenv("ICLASS_PROMO_CODE", ""))
    parser.add_argument("--complete-transaction", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.student_id:
        logger.error("--student-id required")
        return 1
    return run_api_enrollment(args)


if __name__ == "__main__":
    raise SystemExit(main())
