#!/usr/bin/env python3
"""Validate that profiling_native rules:changes patterns in .gitlab-ci.yml stay
in sync with the actual file tree.

Catches two classes of drift:
  1. Under-triggering — a native file is NOT matched by any pattern (build
     breakage would only surface on scheduled/main pipelines).
  2. Over-triggering — a non-native file IS matched (wastes CI).

Raises ``ValueError`` for unclassified file extensions, forcing explicit
categorization when new file types appear. Also warns on stale patterns
that no longer match any tracked file.

Run:
    python scripts/check_profiling_native_coverage.py
    hatch run lint:profiling-native-check          # once wired into hatch.toml
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


ROOT: Path = Path(__file__).resolve().parents[1]

SCAN_DIRS: list[str] = [
    "ddtrace/internal/datadog/profiling",
    "ddtrace/profiling",
    "src/native",
]

# File extensions that belong to the native build graph.
NATIVE_EXTENSIONS: frozenset[str] = frozenset(
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
NATIVE_EXACT_NAMES: frozenset[str] = frozenset({"CMakeLists.txt"})

# File extensions and paths that do NOT affect the native build.
NON_NATIVE_EXTENSIONS: frozenset[str] = frozenset({".py", ".pyi", ".md", ".clang-format", ".gitignore"})
NON_NATIVE_FILES: frozenset[str] = frozenset(
    {
        "ddtrace/internal/datadog/profiling/cmake/tools/infer_checksums.txt",
    }
)

# Patterns that intentionally match files outside SCAN_DIRS.  Any pattern not
# covered by SCAN_DIRS and not in this set is flagged as potentially accidental.
TOP_LEVEL_TRIGGERS: frozenset[str] = frozenset(
    {
        ".gitlab-ci.yml",
        "setup.py",
        "pyproject.toml",
    }
)


# Implementation constants — not user-editable configuration.
_CHANGES_RE: re.Pattern[str] = re.compile(r"^\s+- changes:\s*$")
_LIST_ITEM_RE: re.Pattern[str] = re.compile(r"^\s+- (.+)$")

# Glob tokens → regex replacements for GitLab CI pattern conversion.
_GLOB_RECURSIVE_SEP: str = "/**/"  # zero or more directory levels
_GLOB_RECURSIVE: str = "**"  # match everything (including /)
_GLOB_WILDCARD: str = "*"  # match within a single segment
_GLOB_SINGLE: str = "?"  # match one non-separator char
_RE_RECURSIVE_SEP: str = "(?:/.*)?/"
_RE_RECURSIVE: str = ".*"
_RE_WILDCARD: str = "[^/]*"
_RE_SINGLE: str = "[^/]"
_RE_SPECIAL_CHARS: str = r"\.+^${}()|[]"


def extract_profiling_native_patterns(ci_path: Path) -> list[str]:
    """Extract rules:changes patterns for the profiling_native job."""
    lines: list[str] = ci_path.read_text().splitlines()
    in_job: bool = False
    in_changes: bool = False
    changes_indent: int = 0
    patterns: list[str] = []

    for line in lines:
        stripped: str = line.lstrip()

        # Top-level key (zero indentation) — detect profiling_native:
        if not line.startswith(" ") and not line.startswith("#") and stripped:
            in_job = stripped.startswith("profiling_native:")
            in_changes = False
            continue

        if not in_job:
            continue

        if _CHANGES_RE.match(line):
            in_changes = True
            changes_indent = len(line) - len(stripped)
            continue

        if in_changes:
            m: re.Match[str] | None = _LIST_ITEM_RE.match(line)
            item_indent: int = len(line) - len(stripped) if stripped else 0
            if m and item_indent > changes_indent:
                value: str = m.group(1).strip()
                if not value.startswith("#"):
                    patterns.append(value)
            elif stripped and not stripped.startswith("#") and item_indent <= changes_indent:
                break

    if not patterns:
        raise ValueError("No 'changes' patterns found in profiling_native job")

    return patterns


def tracked_files(dirs: list[str] | None = None) -> list[str]:
    """Return git-tracked files under the given directories."""
    if dirs is None:
        dirs = ["."]

    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "ls-files", "--"] + dirs,
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return [f for f in result.stdout.strip().split("\n") if f]


def is_native(path: str) -> bool:
    """Decide whether *path* belongs to the native build graph.

    Raises ``ValueError`` for unclassified extensions so new file types
    are forced into an explicit bucket.
    """
    if path in NON_NATIVE_FILES:
        return False

    name: str = path.rsplit("/", 1)[-1]
    if name in NATIVE_EXACT_NAMES:
        return True
    if name.startswith("."):
        return False

    ext: str = f".{name.rsplit('.', 1)[-1]}" if "." in name else ""
    if ext in NATIVE_EXTENSIONS:
        return True
    if ext in NON_NATIVE_EXTENSIONS:
        return False

    raise ValueError(f"Unclassified extension for {path!r} — add to NATIVE_EXTENSIONS or NON_NATIVE_EXTENSIONS")


def _glob_to_re(pattern: str) -> re.Pattern[str]:
    """Convert a GitLab CI glob pattern to a compiled regex.

    GitLab's ``/**/`` matches zero or more path segments (including the
    separating ``/``).  Python's :func:`fnmatch.fnmatch` treats ``*`` as
    matching everything *including* ``/``, and has no notion of ``**``, so
    we roll our own converter.  The key insight is that ``/**/`` must be
    treated as a single token meaning "one slash, then optionally any number
    of nested directories".
    """
    result: str = ""
    i: int = 0
    n: int = len(pattern)
    while i < n:
        if pattern[i : i + len(_GLOB_RECURSIVE_SEP)] == _GLOB_RECURSIVE_SEP:
            result += _RE_RECURSIVE_SEP
            i += len(_GLOB_RECURSIVE_SEP)
        elif pattern[i : i + len(_GLOB_RECURSIVE)] == _GLOB_RECURSIVE:
            result += _RE_RECURSIVE
            i += len(_GLOB_RECURSIVE)
        else:
            if pattern[i] == _GLOB_WILDCARD:
                result += _RE_WILDCARD
            elif pattern[i] == _GLOB_SINGLE:
                result += _RE_SINGLE
            elif pattern[i] in _RE_SPECIAL_CHARS:
                result += "\\" + pattern[i]
            else:
                result += pattern[i]
            i += 1

    return re.compile(f"^{result}$")


def path_matches(path: str, patterns: list[str]) -> bool:
    return any(_glob_to_re(p).match(path) for p in patterns)


def main() -> int:
    ci_path: Path = ROOT / ".gitlab-ci.yml"
    patterns: list[str] = extract_profiling_native_patterns(ci_path)
    files: list[str] = tracked_files(SCAN_DIRS)

    under_triggered: list[str] = []
    over_triggered: list[str] = []

    for f in files:
        native: bool = is_native(f)
        matched: bool = path_matches(f, patterns)

        if native:
            if not matched:
                under_triggered.append(f)
        else:
            if matched:
                over_triggered.append(f)

    all_tracked: list[str] = tracked_files()
    stale_patterns: list[str] = [p for p in patterns if not any(_glob_to_re(p).match(f) for f in all_tracked)]

    # Patterns that match files only outside SCAN_DIRS must be explicitly
    # allowed via TOP_LEVEL_TRIGGERS — catches accidentally broad patterns.
    scanned_set: frozenset[str] = frozenset(files)
    unscoped_patterns: list[str] = [
        p
        for p in patterns
        if p not in TOP_LEVEL_TRIGGERS
        and p not in stale_patterns
        and not any(_glob_to_re(p).match(f) for f in scanned_set)
    ]

    errors: int = 0

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

    if unscoped_patterns:
        errors += 1
        print(
            f"FAIL  {len(unscoped_patterns)} pattern(s) match files only outside SCAN_DIRS (add to TOP_LEVEL_TRIGGERS):"
        )
        for p in sorted(unscoped_patterns):
            print(f"      {p}")
        print()

    if stale_patterns:
        print(f"WARN  {len(stale_patterns)} pattern(s) match no tracked files (stale?):")
        for p in sorted(stale_patterns):
            print(f"      {p}")
        print()

    if not errors:
        print(f"OK    {len(files)} files checked, {len(patterns)} patterns validated, no issues found")

    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
