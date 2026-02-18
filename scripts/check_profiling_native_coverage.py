#!/usr/bin/env python3
"""Validate that profiling_native rules:changes patterns in .gitlab-ci.yml stay
in sync with the actual file tree.

Catches two classes of drift:
  1. Under-triggering — a native file is NOT matched by any pattern (build
     breakage would only surface on scheduled/main pipelines).
  2. Over-triggering — a non-native file IS matched (wastes CI).

Also flags stale patterns that no longer match any tracked file, and unknown
extensions that need explicit classification.

Run:
    python scripts/check_profiling_native_coverage.py
    hatch run lint:profiling-native-check          # once wired into hatch.toml
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[1]

# Directories that the OLD broad globs covered — any native file here must be
# matched by the new extension-based patterns.
SCAN_DIRS = [
    "ddtrace/internal/datadog/profiling",
    "ddtrace/profiling",
    "src/native",
]

# File extensions that belong to the native build graph.
NATIVE_EXTENSIONS = frozenset(
    {
        ".cpp",
        ".cc",
        ".h",
        ".hpp",  # C / C++
        ".pyx",  # Cython
        ".cmake",  # CMake modules
        ".sh",  # build scripts
        ".supp",  # valgrind suppressions
        ".rs",  # Rust
        ".toml",  # Cargo.toml, .cargo/config.toml
        ".lock",  # Cargo.lock
    }
)
NATIVE_EXACT_NAMES = frozenset({"CMakeLists.txt"})

# File extensions that must NOT trigger.
NON_NATIVE_EXTENSIONS = frozenset({".py", ".pyi", ".md"})

# Files that don't affect the build and are acceptable gaps (not native, but
# also not worth adding as triggers).
KNOWN_GAPS = frozenset(
    {
        ".clang-format",
        ".gitignore",
    }
)
KNOWN_GAP_FILES = frozenset(
    {
        # Checksum for a static-analysis download; doesn't affect build output
        "ddtrace/internal/datadog/profiling/cmake/tools/infer_checksums.txt",
    }
)


def extract_profiling_native_patterns(ci_path: Path) -> list[str]:
    """Parse .gitlab-ci.yml and return the rules:changes patterns for
    the profiling_native job.
    """
    yaml = YAML()
    yaml.allow_duplicate_keys = True
    data = yaml.load(ci_path)
    rules = data["profiling_native"]["rules"]
    for rule in rules:
        if "changes" in rule:
            return list(rule["changes"])
    raise ValueError("No 'changes' rule found in profiling_native job")


def tracked_files(dirs: list[str]) -> list[str]:
    """Return git-tracked files under the given directories."""
    result = subprocess.run(
        ["git", "ls-files", "--"] + dirs,
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return [f for f in result.stdout.strip().split("\n") if f]


def classify_extension(path: str) -> str:
    """Return 'native', 'non_native', 'known_gap', or 'unknown'."""
    if path in KNOWN_GAP_FILES:
        return "known_gap"
    name = path.rsplit("/", 1)[-1]
    if name in NATIVE_EXACT_NAMES:
        return "native"
    ext = ("." + name.rsplit(".", 1)[-1]) if "." in name else ""
    if ext in NATIVE_EXTENSIONS:
        return "native"
    if ext in NON_NATIVE_EXTENSIONS:
        return "non_native"
    if ext in KNOWN_GAPS or name.startswith("."):
        return "known_gap"
    return "unknown"


def _glob_to_re(pattern: str) -> re.Pattern[str]:
    """Convert a GitLab CI glob pattern to a compiled regex.

    GitLab's ``/**/`` matches zero or more path segments (including the
    separating ``/``).  Python's :func:`fnmatch.fnmatch` treats ``*`` as
    matching everything *including* ``/``, and has no notion of ``**``, so
    we roll our own converter.  The key insight is that ``/**/`` must be
    treated as a single token meaning "one slash, then optionally any number
    of nested directories".
    """
    result = ""
    i, n = 0, len(pattern)
    while i < n:
        if pattern[i : i + 4] == "/**/":
            # /**/ = zero or more directory levels between two fixed parts
            result += "(?:/.*)?/"
            i += 4
        elif pattern[i : i + 2] == "**":
            result += ".*"
            i += 2
        elif pattern[i] == "*":
            result += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            result += "[^/]"
            i += 1
        elif pattern[i] in r"\.+^${}()|[]":
            result += "\\" + pattern[i]
            i += 1
        else:
            result += pattern[i]
            i += 1
    return re.compile("^" + result + "$")


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(_glob_to_re(p).match(path) for p in patterns)


def main() -> int:
    ci_path = ROOT / ".gitlab-ci.yml"
    patterns = extract_profiling_native_patterns(ci_path)
    files = tracked_files(SCAN_DIRS)

    under_triggered: list[str] = []  # native file not matched
    over_triggered: list[str] = []  # non-native file matched
    unknown_files: list[str] = []  # extension needs classification
    stale_patterns: list[str] = []  # pattern matches nothing

    for f in files:
        cat = classify_extension(f)
        matched = matches_any(f, patterns)

        if cat == "native" and not matched:
            under_triggered.append(f)
        elif cat == "non_native" and matched:
            over_triggered.append(f)
        elif cat == "unknown":
            unknown_files.append(f)

    all_tracked = tracked_files(["."])
    for p in patterns:
        if not any(_glob_to_re(p).match(f) for f in all_tracked):
            stale_patterns.append(p)

    errors = 0

    if under_triggered:
        errors += 1
        print(f"FAIL  {len(under_triggered)} native file(s) NOT matched by any pattern (under-triggering):")
        for f in sorted(under_triggered):
            print(f"      {f}")
        print()

    if over_triggered:
        errors += 1
        print(f"FAIL  {len(over_triggered)} non-native file(s) matched by a pattern (over-triggering):")
        for f in sorted(over_triggered):
            print(f"      {f}")
        print()

    if unknown_files:
        errors += 1
        print(f"FAIL  {len(unknown_files)} file(s) with unclassified extensions (add to NATIVE/NON_NATIVE/KNOWN_GAPS):")
        for f in sorted(unknown_files):
            print(f"      {f}")
        print()

    if stale_patterns:
        print(f"WARN  {len(stale_patterns)} pattern(s) match no tracked files (stale?):")
        for p in sorted(stale_patterns):
            print(f"      {p}")
        print()

    if not errors:
        print(f"OK    {len(files)} files checked, {len(patterns)} patterns validated, no issues found")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
