"""Entrypoint for `python3 -m touchmimic`."""

import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())

