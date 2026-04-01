#!/usr/bin/env python3
"""Developer convenience script for cleaning build artifacts.

Delegates to _build.clean which contains the canonical implementation.
No build dependencies required.

Usage:
    python scripts/clean.py
    uv run scripts/clean.py
"""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _build.clean import main


if __name__ == "__main__":
    sys.exit(main())
