#!/usr/bin/env python3
"""Backward-compatible launcher; forwards to ``iclasspro.py`` with the same arguments."""

import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_MAIN = os.path.join(_ROOT, "iclasspro.py")


def main() -> int:
    return subprocess.call([sys.executable, _MAIN] + sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
