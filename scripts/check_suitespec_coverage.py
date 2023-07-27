#!/usr/bin/env python3

import fnmatch
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parents[1]))

import tests.suitespec as spec  # noqa


ROOT = Path(__file__).parents[1]
GITIGNORE_FILE = ROOT / ".gitignore"
DDTRACE_PATH = ROOT / "ddtrace"

ignore_patterns = {_ for _ in GITIGNORE_FILE.read_text().strip().splitlines() if _ and not _.startswith("#")}
files = {
    f
    for f in (_.relative_to(ROOT) for _ in DDTRACE_PATH.glob("**/*") if _.is_file())
    if not any(f.match(p) for p in ignore_patterns)
}

spec_patterns = {_ for suite in spec.get_suites() for _ in spec.get_patterns(suite)}

uncovered_files = {f for f in files if not any(fnmatch.fnmatch(f, p) for p in spec_patterns)}
if uncovered_files:
    print("Files not covered by any suite spec:", len(uncovered_files))
    for f in sorted(uncovered_files):
        print(f"  {f}")
else:
    print("All files are covered by suite specs")

sys.exit(bool(uncovered_files))
