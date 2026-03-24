#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "cython",
#   "cmake>=3.24.2,<3.28",
#   "setuptools-rust<2",
#   "setuptools-scm[toml]>=4",
# ]
# ///
"""Run `python setup.py clean --all` with the required build dependencies.

Usage:
    uv run scripts/clean.py
"""
import runpy
import sys

sys.argv = ["setup.py", "clean", "--all"]
runpy.run_path("setup.py", run_name="__main__")
