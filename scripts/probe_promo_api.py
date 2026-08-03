#!/usr/bin/env python3
"""Try JWT promo endpoints after adding a class to cart (dry-run helper)."""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"), override=True)

from iclasspro_jwt import IClassProAPIClient, _default_portal_slug, clear_cart_before_enrollment


def main() -> int:
    email = os.environ["ICLASS_EMAIL"]
    password = os.environ["ICLASS_PASSWORD"]
    promo = os.getenv("ICLASS_PROMO_CODE", "").strip()
    student_id = int(os.getenv("ICLASS_STUDENT_ID", "0"))
    if not promo:
        print("ICLASS_PROMO_CODE not set", file=sys.stderr)
        return 1

    schedule_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        _ROOT, "schedules/tmp/test_sunday_el_segundo.json"
    )
    import json

    with open(schedule_path) as f:
        schedule = json.load(f)

    clear_cart_before_enrollment(email, password, schedule=schedule)

    from iclasspro_jwt import enroll_from_schedule

    portal = _default_portal_slug()
    client = IClassProAPIClient(email, password, portal)
    client.login()

    summary, _ = enroll_from_schedule(
        client,
        schedule,
        student_id,
        promo_code=None,
        complete_transaction=False,
    )
    print("Enroll summary:", summary)

    loc_id = 1
    before = client.validate_cart(loc_id)
    print("Before promo totalCartDueAmount:", before.get("totalCartDueAmount"))

    try:
        client.apply_promo_code(promo, location_id=loc_id)
    except Exception as exc:
        print("apply_promo_code failed:", exc)
        return 1
    after = client.validate_cart(loc_id)
    due = after.get("totalCartDueAmount")
    print("totalCartDueAmount:", due, "promoCodes:", after.get("promoCodes"))
    return 0 if due == 0 or due == 0.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
