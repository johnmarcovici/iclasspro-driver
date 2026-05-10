#!/usr/bin/env python3
"""Backward-compatible launcher; prefer ``python iclasspro.py --driver api``."""

import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_MAIN = os.path.join(_ROOT, "iclasspro.py")


def main() -> int:
    argv = sys.argv[1:]
    if "--driver" not in argv:
        argv = ["--driver", "api"] + argv
    return subprocess.call([sys.executable, _MAIN] + argv)


if __name__ == "__main__":
    raise SystemExit(main())
