#!/usr/bin/env python3
"""Test JWT login against iClassPro (reads credentials from .env)."""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"), override=True)

from iclasspro_jwt import IClassProAPIClient, _default_portal_slug  # noqa: E402


def main() -> int:
    email = os.getenv("ICLASS_EMAIL", "").strip()
    password = os.getenv("ICLASS_PASSWORD", "").strip()
    portal = os.getenv("ICLASS_PORTAL", "").strip() or _default_portal_slug()

    if not email or not password:
        print("Set ICLASS_EMAIL and ICLASS_PASSWORD in .env", file=sys.stderr)
        return 1

    client = IClassProAPIClient(email, password, portal)
    try:
        token = client.login()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"OK: login succeeded for account={portal} (token length {len(token)})")
    try:
        methods = client.get_payment_methods()
        print(f"Payment methods on file: {len(methods)}")
    except Exception as exc:
        print(f"WARN: could not list payment methods: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
