#!/usr/bin/env python3
"""Top-level standalone application entrypoint for TouchMimic."""

from __future__ import annotations

import os
import sys

# Ensure repository root is on sys.path if running from source
ROOT_DIR = os.path.dirname(os.path.realpath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from touchmimic.cli import main

if __name__ == "__main__":
    sys.exit(main())
