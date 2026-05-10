#!/usr/bin/env python3
"""iclasspro_api.py — Pure requests-based iClassPro enrollment driver.

Drop-in replacement for iclasspro.py that uses direct HTTP calls to the
iClassPro JWT REST API (https://app.iclasspro.com/api/jwt/v1/) instead of
browser automation.  Accepts the same CLI flags so app.py can use it
interchangeably.

Usage (same as iclasspro.py):
    python iclasspro_api.py --email you@example.com --password s3cr3t \
        --student-id 7268 --schedule schedules/short_schedule.json \
        --promo-code MYCODE --complete-transaction

Scrape mode:
    python iclasspro_api.py --email you@example.com --password s3cr3t \
        --student-id 7268 --scrape --scrape-days tuesday,wednesday \
        --scrape-locations "El Segundo,Culver"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_BASE = "https://app.iclasspro.com/api/jwt/v1"
LOGIN_URL = f"{API_BASE}/login"

WEEK_DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAY_TO_INDEX = {d.lower(): i + 1 for i, d in enumerate(WEEK_DAYS)}

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(am|pm)", re.IGNORECASE)

load_dotenv(override=True)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class EnrollmentSkipped(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
def _setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


# ---------------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------------
def _normalize_time(raw: str) -> str:
    """Normalise a time string to canonical 'H:MMam' form."""
    raw = raw.strip().lower().replace(" ", "")
    m = _TIME_RE.match(raw)
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


def _times_match(schedule_time: str, class_time: str) -> bool:
    return _normalize_time(schedule_time) == _normalize_time(class_time)


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------
class IClassProAPIClient:
    """Thin wrapper around the iClassPro JWT REST API."""

    def __init__(self, email: str, password: str, portal: str = "scaq") -> None:
        self.email = email
        self.password = password
        self.portal = portal
        self.token: Optional[str] = None
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://portal.iclasspro.com",
            "Referer": f"https://portal.iclasspro.com/{portal}/",
        })

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def login(self) -> str:
        """Authenticate and store the JWT token."""
        logger.info("Logging in as %s...", self.email)
        payload = {
            "email": self.email,
            "password": self.password,
            "portal": self.portal,
        }
        resp = self.session.post(LOGIN_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # Token may be top-level or nested under "data"
        self.token = (
            data.get("token")
            or data.get("access_token")
            or data.get("jwt")
            or (data.get("data") or {}).get("token")
        )
        if not self.token:
            raise RuntimeError(f"Login response contained no token: {data}")
        logger.info("Login successful.")
        return self.token

    def _get(self, path: str, params: Optional[dict] = None, **kwargs) -> Any:
        """Authenticated GET — appends token automatically."""
        if not self.token:
            self.login()
        p = dict(params or {})
        p["token"] = self.token
        resp = self.session.get(f"{API_BASE}/{path}", params=p, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict, params: Optional[dict] = None, **kwargs) -> Any:
        """Authenticated POST — appends token automatically."""
        if not self.token:
            self.login()
        p = dict(params or {})
        p["token"] = self.token
        resp = self.session.post(f"{API_BASE}/{path}", json=payload, params=p, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Data retrieval
    # ------------------------------------------------------------------
    def get_students(self) -> list:
        return self._get("students")

    def get_sessions(self, student_ids: str = "") -> list:
        return self._get("sessions", params={"students": student_ids})

    def get_classes(
        self,
        *,
        day_index: Optional[int] = None,
        location_filter: Optional[str] = None,
        student_id: Optional[int] = None,
    ) -> list:
        """Fetch classes with optional day/location/student filters."""
        params: dict[str, Any] = {}
        if day_index is not None:
            params["dayOfWeek"] = day_index
        if student_id is not None:
            params["selectedStudentIds"] = student_id
        classes = self._get("classes", params=params)
        if location_filter:
            lf = location_filter.lower()
            classes = [c for c in classes if lf in (c.get("location") or c.get("name") or "").lower()]
        return classes

    def get_class_detail(self, class_id: int, student_id: Optional[int] = None) -> dict:
        """Return full detail for a single class including sessions."""
        params: dict[str, Any] = {}
        if student_id is not None:
            params["selectedStudentIds"] = student_id
        return self._get(f"classes/{class_id}", params=params)

    def get_cart(self) -> list:
        return self._get("cart")

    def get_payment_methods(self) -> list:
        return self._get("family-payment-method")

    # ------------------------------------------------------------------
    # Cart & checkout
    # ------------------------------------------------------------------
    def add_to_cart(self, class_id: int, session_id: int, student_id: int) -> dict:
        """Add a class/session to the cart."""
        payload = {
            "objectId": class_id,
            "objectType": "session",
            "sessionId": session_id,
            "selectedStudentIds": [student_id],
        }
        logger.info(
            "Adding to cart: class=%d session=%d student=%d",
            class_id, session_id, student_id,
        )
        return self._post("validate-cart-item", payload)

    def apply_promo_code(self, code: str) -> dict:
        """Apply a promo/discount code to the cart."""
        logger.info("Applying promo code: %s", code)
        return self._post("cart/promo-code", {"promoCode": code})

    def checkout(
        self,
        payment_method_id: Optional[int] = None,
        *,
        complete_transaction: bool = True,
    ) -> dict:
        """Submit the cart for payment.

        If complete_transaction is False this is a dry-run — logs intent but
        does not submit (mirrors --complete-transaction semantics in iclasspro.py).
        """
        if not complete_transaction:
            logger.info("Dry-run: skipping checkout (complete_transaction=False).")
            return {"dry_run": True, "message": "Transaction not submitted (dry-run mode)."}

        payload: dict[str, Any] = {}
        if payment_method_id is not None:
            payload["paymentMethodId"] = payment_method_id

        logger.info("Submitting checkout...")
        return self._post("checkout", payload)


# ---------------------------------------------------------------------------
# High-level enrollment logic
# ---------------------------------------------------------------------------
def _find_matching_class(
    classes: list,
    location: str,
    time_str: str,
    day: str,
) -> Optional[dict]:
    """Return the first class matching location + time + day."""
    day_idx = DAY_TO_INDEX.get(day.lower())
    for cls in classes:
        cls_loc = (cls.get("location") or cls.get("name") or "").lower()
        cls_time = str(cls.get("startTime") or cls.get("time") or cls.get("start_time") or "")
        cls_day = cls.get("dayOfWeek") or cls.get("day_of_week") or 0
        if isinstance(cls_day, str):
            cls_day = DAY_TO_INDEX.get(cls_day.lower(), 0)

        loc_ok  = location.lower() in cls_loc
        time_ok = _times_match(time_str, cls_time)
        day_ok  = (day_idx is None) or (cls_day == day_idx)

        if loc_ok and time_ok and day_ok:
            return cls
    return None


def enroll_from_schedule(
    client: IClassProAPIClient,
    schedule: list,
    student_id: int,
    promo_code: Optional[str] = None,
    complete_transaction: bool = False,
) -> list:
    """Enroll in each entry in schedule.  Returns a per-entry summary list."""
    summary = []

    for entry in schedule:
        location = entry.get("Location", "")
        time_str = entry.get("Time", "")
        day      = entry.get("Day", "")
        label    = f"{location} {day} {time_str}"

        logger.info("── Processing: %s", label)
        result: dict[str, Any] = {
            "label": label, "location": location, "time": time_str, "day": day,
        }

        try:
            day_idx = DAY_TO_INDEX.get(day.lower())
            if day_idx is None:
                raise ValueError(f"Unknown day: {day!r}")

            # 1. Fetch + filter classes
            classes = client.get_classes(
                day_index=day_idx, location_filter=location, student_id=student_id,
            )
            logger.info("  %d class(es) found for %s on %s", len(classes), location, day)

            # 2. Match by time
            matched = _find_matching_class(classes, location, time_str, day)
            if matched is None:
                raise ValueError(f"No class found for {label}")

            class_id = matched.get("id") or matched.get("classId")
            logger.info("  Matched class id=%s: %s", class_id, matched.get("name") or matched.get("location"))

            # 3. Get session id
            detail   = client.get_class_detail(class_id, student_id=student_id)
            sessions = (
                detail.get("sessions")
                or (detail.get("data") or {}).get("sessions")
                or []
            )
            if not sessions:
                raw = client.get_sessions(student_ids=str(student_id))
                sessions = [s for s in raw if str(s.get("classId")) == str(class_id)]
            if not sessions:
                raise ValueError(f"No sessions found for class {class_id}")

            session    = sessions[0]
            session_id = session.get("id") or session.get("sessionId")
            logger.info("  Session id=%s", session_id)

            # 4. Guard: already enrolled?
            if any(
                str(s.get("status", "")).lower() in ("enrolled", "active")
                for s in sessions
                if str(s.get("studentId")) == str(student_id)
            ):
                raise EnrollmentSkipped(f"Already enrolled in {label}")

            # 5. Add to cart
            client.add_to_cart(class_id, session_id, student_id)

            # 6. Promo code
            if promo_code:
                try:
                    client.apply_promo_code(promo_code)
                except Exception as exc:
                    logger.warning("  Promo code failed (continuing): %s", exc)

            # 7. Checkout (or dry-run)
            pms    = client.get_payment_methods()
            pm_id  = pms[0].get("id") if pms else None
            result_checkout = client.checkout(pm_id, complete_transaction=complete_transaction)
            logger.info("  Checkout: %s", str(result_checkout)[:120])

            result["status"]  = "Success"
            result["details"] = result_checkout

        except EnrollmentSkipped as exc:
            logger.info("  Skipped: %s", exc.reason)
            result["status"]  = "Skipped"
            result["details"] = exc.reason

        except Exception as exc:
            logger.error("  Failed to enroll in %s: %s", label, exc)
            result["status"]  = "Failed"
            result["details"] = str(exc)

        summary.append(result)

    return summary


# ---------------------------------------------------------------------------
# Scrape / discovery mode
# ---------------------------------------------------------------------------
def scrape_classes(
    client: IClassProAPIClient,
    student_id: int,
    days: Optional[list] = None,
    locations: Optional[list] = None,
) -> list:
    """Print and return available classes matching the given filters."""
    results = []
    day_indices = (
        [DAY_TO_INDEX[d.lower()] for d in days if d.lower() in DAY_TO_INDEX]
        if days else None
    )

    targets = day_indices or [None]
    for day_idx in targets:
        day_name = WEEK_DAYS[day_idx - 1] if day_idx else "All days"
        logger.info("Scraping classes for %s...", day_name)
        classes = client.get_classes(day_index=day_idx, student_id=student_id)

        for cls in classes:
            cls_loc = cls.get("location") or cls.get("name") or ""
            if locations and not any(lf.lower() in cls_loc.lower() for lf in locations):
                continue
            cls_day_idx = cls.get("dayOfWeek") or 0
            resolved_day = (
                WEEK_DAYS[cls_day_idx - 1] if 1 <= cls_day_idx <= 7 else (day_name if day_idx else "Unknown")
            )
            time_raw = cls.get("startTime") or cls.get("time") or ""
            entry = {
                "Location": cls_loc,
                "Time": str(time_raw),
                "Day": resolved_day,
                "_class_id": cls.get("id"),
                "_instructor": cls.get("instructor") or cls.get("instructorName") or "",
                "_program": cls.get("program") or cls.get("programName") or "",
            }
            print(
                f"  [{resolved_day}] {cls_loc} at {time_raw}"
                f"  (id={cls.get('id')}, instructor={entry['_instructor']})"
            )
            results.append(entry)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "iClassPro enrollment via direct REST API — requests-based driver.\n"
            "Drop-in CLI replacement for iclasspro.py (same flags, no browser required)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--email",       default=os.getenv("ICLASS_EMAIL"),       help="Account email")
    p.add_argument("--password",    default=os.getenv("ICLASS_PASSWORD"),    help="Account password")
    p.add_argument("--student-id",  default=os.getenv("ICLASS_STUDENT_ID"),  type=int, help="Student ID")
    p.add_argument("--portal",      default="scaq",                          help="Portal slug (default: scaq)")

    # Enrollment mode
    p.add_argument("--schedule",    default=os.getenv("ICLASS_SCHEDULE"),    help="Path to schedule JSON")
    p.add_argument("--promo-code",  default=os.getenv("ICLASS_PROMO_CODE"),  help="Promo/discount code")
    p.add_argument(
        "--complete-transaction",
        action="store_true",
        default=os.getenv("ICLASS_COMPLETE_TRANSACTION", "0").lower() in ("1", "true", "yes"),
        help="Actually finalise payment (default: dry-run)",
    )

    # Scrape mode
    p.add_argument("--scrape",            action="store_true", help="Discovery / scrape mode")
    p.add_argument("--scrape-days",       help="Comma-separated days to search (e.g. tuesday,wednesday)")
    p.add_argument("--scrape-locations",  help="Comma-separated location filter (e.g. 'El Segundo')")
    p.add_argument("--deep-scrape",       action="store_true", help="(kept for CLI parity, unused)")

    # Compat flags
    p.add_argument("--send-email",  action="store_true", help="(kept for CLI parity, unused)")
    p.add_argument("--deep-debug",  action="store_true", help="Enable DEBUG logging")

    return p


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    _setup_logging(logging.DEBUG if args.deep_debug else logging.INFO)

    if not args.email or not args.password:
        logger.error("--email and --password required (or ICLASS_EMAIL / ICLASS_PASSWORD env vars).")
        return 1
    if not args.student_id:
        logger.error("--student-id required (or ICLASS_STUDENT_ID env var).")
        return 1

    client = IClassProAPIClient(args.email, args.password, portal=args.portal)
    try:
        client.login()
    except Exception as exc:
        logger.error("Login failed: %s", exc)
        return 1

    # ── Scrape mode ──────────────────────────────────────────────────────────
    if args.scrape:
        days      = [d.strip() for d in args.scrape_days.split(",")]      if args.scrape_days      else None
        locations = [l.strip() for l in args.scrape_locations.split(",")]  if args.scrape_locations else None
        print("\n=== Available Classes ===")
        found = scrape_classes(client, args.student_id, days=days, locations=locations)
        print(f"\nFound {len(found)} class(es) matching filters.")
        return 0

    # ── Enrollment mode ───────────────────────────────────────────────────────
    if not args.schedule:
        logger.error("--schedule required for enrollment mode.")
        return 1

    try:
        with open(args.schedule) as fh:
            schedule = json.load(fh)
    except Exception as exc:
        logger.error("Could not read schedule %r: %s", args.schedule, exc)
        return 1

    if not schedule:
        logger.error("Schedule is empty.")
        return 1

    logger.info(
        "Starting enrollment for %d class(es) [complete_transaction=%s]",
        len(schedule), args.complete_transaction,
    )

    summary = enroll_from_schedule(
        client, schedule, args.student_id,
        promo_code=args.promo_code or None,
        complete_transaction=args.complete_transaction,
    )

    # ── Report ────────────────────────────────────────────────────────────────
    print("\n=== Enrollment Run Report ===")
    success = sum(1 for r in summary if r["status"] == "Success")
    skipped = sum(1 for r in summary if r["status"] == "Skipped")
    failed  = sum(1 for r in summary if r["status"] == "Failed")

    for r in summary:
        icon = {"Success": "\u2714", "Skipped": "\u27f3", "Failed": "\u2718"}.get(r["status"], "?")
        print(f"  {icon} [{r['status']:8s}] {r['label']}")
        if r.get("details") and r["status"] != "Success":
            print(f"             {r['details']}")

    print(f"\nSummary: {success} enrolled, {skipped} skipped, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
