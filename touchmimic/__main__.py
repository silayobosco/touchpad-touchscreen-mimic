"""Entrypoint for `python -m touchmimic`."""

from __future__ import annotations

import os
import sys

if not __package__:
    pkg_dir = os.path.dirname(os.path.realpath(__file__))
    root_dir = os.path.dirname(pkg_dir)
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)
    from touchmimic.cli import main
else:
    from .cli import main

if __name__ == "__main__":
    sys.exit(main())
