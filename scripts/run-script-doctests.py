#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lxml==5.3.0",
#   "packaging==23.1",
#   "ruamel.yaml==0.18.6",
#   "vcrpy==6.0.2",
# ]
# ///
"""Run doctests for helper scripts and test specifications.

Usage:
    uv run scripts/run-script-doctests.py
"""

import doctest
import sys


_FILES = [
    "scripts/get-target-milestone.py",
    "scripts/needs_testrun.py",
    "tests/suitespec.py",
]

failures = 0
for path in _FILES:
    result = doctest.testfile(path, module_relative=False, verbose=False)
    failures += result.failed

sys.exit(failures)
