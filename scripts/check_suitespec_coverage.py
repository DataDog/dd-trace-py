#!/usr/bin/env python3

import fnmatch
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parents[1]))

import tests.suitespec as spec  # noqa


ROOT = Path(__file__).parents[1]
GITIGNORE_FILE = ROOT / ".gitignore"
DDTRACE_PATH = ROOT / "ddtrace"
TEST_PATH = ROOT / "tests"

ignore_patterns = {_ for _ in GITIGNORE_FILE.read_text().strip().splitlines() if _ and not _.startswith("#")}


def uncovered(path: Path) -> set[str]:
    files = {
        f
        for f in (_.relative_to(ROOT) for _ in path.glob("**/*") if _.is_file())
        if not any(f.match(p) for p in ignore_patterns)
    }

    spec_patterns = {_ for suite in spec.get_suites() for _ in spec.get_patterns(suite)}

    return {f for f in files if not any(fnmatch.fnmatch(f, p) for p in spec_patterns)}


uncovered_sources = uncovered(DDTRACE_PATH)
uncovered_tests = uncovered(TEST_PATH)

if uncovered_sources:
    print("Source files not covered by any suite spec:", len(uncovered_sources))
    for f in sorted(uncovered_sources):
        print(f"  {f}")
if uncovered_tests:
    print("Test scripts not covered by any suite spec:", len(uncovered_tests))
    for f in sorted(uncovered_tests):
        print(f"  {f}")
if not uncovered_sources and not uncovered_tests:
    print("All files are covered by suite specs")

sys.exit(bool(uncovered_sources | uncovered_tests))
