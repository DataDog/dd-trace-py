#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lxml==5.3.0",
#   "packaging==23.1",
#   "ruamel.yaml==0.18.6",
#   "vcrpy==6.0.2",
# ]
# ///
"""Run doctests for helper scripts and test specifications."""

import doctest
import importlib.util
import sys


_FILES = [
    "scripts/get-target-milestone.py",
    "scripts/needs_testrun.py",
    "tests/suitespec.py",
]

failures = 0
for path in _FILES:
    spec = importlib.util.spec_from_file_location("_doctest_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = doctest.testmod(module, verbose=False)
    failures += result.failed

sys.exit(failures)
