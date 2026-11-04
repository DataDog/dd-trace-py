#!/usr/bin/env python3

import fnmatch
import functools
import os
from pathlib import Path
import re
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


def owners(path: str) -> str:
    return ", ".join(CODEOWNERS.of(path))


@functools.cache
def _ignore_matcher(ignore_patterns: frozenset[str]) -> t.Callable[[Path], bool]:
    """Return a function that gives the same result as Path.match on any of ignore_patterns.

    Path.match keeps a small cache of compiled patterns on some Python
    versions, and its rules changed between versions. These explicit rules
    give the same result on every version.
    """
    rules = []
    last_parts = []
    for pattern in ignore_patterns:
        # Path.match drops empty and "." parts, and an anchored pattern never
        # matches a relative path.
        parts = [_ for _ in pattern.split("/") if _ and _ != "."]
        if not parts:
            raise ValueError("empty pattern")
        if pattern.startswith("/"):
            continue
        # Path.match compares the parts from the right, with fnmatch rules for each part.
        rules.append(tuple(re.compile(fnmatch.translate(_)).match for _ in reversed(parts)))
        last_parts.append(fnmatch.translate(parts[-1]))
    if not rules:
        return lambda path: False

    # Most file names fail the last part of every rule. One combined regex for
    # the last parts removes those files before the loop over the rules.
    matches_a_last_part = re.compile("|".join(last_parts)).match

    def is_ignored(path: Path) -> bool:
        parts = path.parts[::-1]
        if not parts or not matches_a_last_part(parts[0]):
            return False
        return any(len(rule) <= len(parts) and all(m(_) for m, _ in zip(rule, parts)) for rule in rules)

    return is_ignored


def filter_ignored(
    paths: t.Iterable[Path], root: Path = ROOT, ignore_patterns: t.Collection[str] = IGNORE_PATTERNS
) -> set[Path]:
    is_ignored = _ignore_matcher(frozenset(ignore_patterns))
    return {f for f in (_.relative_to(root) for _ in paths if _.is_file()) if not is_ignored(f)}


def uncovered(
    path: Path,
    root: Path = ROOT,
    spec_patterns: t.Collection[str] = SPEC_PATTERNS,
    ignore_patterns: t.Collection[str] = IGNORE_PATTERNS,
) -> set[str]:
    if not spec_patterns:
        return {str(f) for f in filter_ignored(path.glob("**/*"), root, ignore_patterns)}

    # The fnmatch module caches 256 compiled patterns on Python 3.10, and the
    # suitespecs have more patterns than that. A call to fnmatch.fnmatch for
    # each pair of file and pattern then compiles a new regex almost every
    # time. One combined regex gives the same result as fnmatch.fnmatch on any
    # of the patterns, on every Python version.
    is_covered = re.compile("|".join(fnmatch.translate(os.path.normcase(p)) for p in spec_patterns)).match
    return {
        str(f)
        for f in filter_ignored(path.glob("**/*"), root, ignore_patterns)
        if not is_covered(os.path.normcase(str(f)))
    }


def _matches_kept_file(pattern: str, root: Path, ignore_patterns: t.Collection[str]) -> bool:
    is_ignored = _ignore_matcher(frozenset(ignore_patterns))
    return any(f.is_file() and not is_ignored(f.relative_to(root)) for f in root.glob(pattern.lstrip("!")))


def unmatched(
    root: Path = ROOT,
    spec_patterns: t.Collection[str] = SPEC_PATTERNS,
    ignore_patterns: t.Collection[str] = IGNORE_PATTERNS,
) -> set[str]:
    return {pattern for pattern in spec_patterns if not _matches_kept_file(pattern, root, ignore_patterns)}


def main() -> int:
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

    return bool(uncovered_sources | uncovered_tests | unmatched_patterns)


if __name__ == "__main__":
    sys.exit(main())
