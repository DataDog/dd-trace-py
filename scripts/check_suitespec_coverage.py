#!/usr/bin/env python3

import fnmatch
from pathlib import Path
import sys
import typing as t


sys.path.insert(0, str(Path(__file__).parents[1] / "ddtrace" / "internal"))
sys.path.insert(0, str(Path(__file__).parents[1]))

import tests.suitespec as spec  # noqa
from codeowners import Codeowners  # noqa

CODEOWNERS = Codeowners()

ROOT = Path(__file__).parents[1]
GITIGNORE_FILE = ROOT / ".gitignore"
DDTRACE_PATH = ROOT / "ddtrace"
TEST_PATH = ROOT / "tests"

IGNORE_PATTERNS = {_ for _ in GITIGNORE_FILE.read_text().strip().splitlines() if _ and not _.startswith("#")}
SPEC_PATTERNS = {_ for suite in spec.get_suites() for _ in spec.get_patterns(suite)}

# Ignore any embedded documentation
IGNORE_PATTERNS.add("**/*.md")
# The aioredis integration is deprecated and untested
IGNORE_PATTERNS.add("ddtrace/contrib/aioredis/*")


def owners(path: str) -> str:
    return ", ".join(CODEOWNERS.of(path))


def filter_ignored(paths: t.Iterable[Path]) -> set[Path]:
    return {
        f for f in (_.relative_to(ROOT) for _ in paths if _.is_file()) if not any(f.match(p) for p in IGNORE_PATTERNS)
    }


def uncovered(path: Path) -> set[str]:
    return {str(f) for f in filter_ignored(path.glob("**/*")) if not any(fnmatch.fnmatch(f, p) for p in SPEC_PATTERNS)}


def unmatched() -> set[str]:
    return {pattern for pattern in SPEC_PATTERNS if not filter_ignored(ROOT.glob(pattern))}


uncovered_sources = uncovered(DDTRACE_PATH)
uncovered_tests = uncovered(TEST_PATH)
unmatched_patterns = unmatched()

if uncovered_sources:
    print(f"▶️ {len(uncovered_sources)} source files not covered by any suite specs:")
    for f in sorted(uncovered_sources):
        print(f"    {f}\t({owners(f)})")
    print()
if uncovered_tests:
    print(f"🧪 {len(uncovered_tests)} test files not covered by any suite specs:")
    for f in sorted(uncovered_tests):
        print(f"    {f}\t({owners(f)})")
    print()
if not uncovered_sources and not uncovered_tests:
    print("✨ 🍰 ✨ All files are covered by suite specs")

if unmatched_patterns:
    print(f"🧹 {len(unmatched_patterns)} unmatched patterns:")
    for p in sorted(unmatched_patterns):
        print(f"    {p}")
    print()
else:
    print("✨ 🧹 ✨ All patterns are matching")

sys.exit(bool(uncovered_sources | uncovered_tests | unmatched_patterns))
