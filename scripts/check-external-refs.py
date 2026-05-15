#!/usr/bin/env python3
"""Validate that external references in CI files are centralized.

This script enforces the policy described in
``docs/github-org-migration-audit.md`` and ``.gitlab/external-refs.yml``:
new references to internal Datadog GitHub repos, ``*.ddbuild.io`` endpoints,
cross-project GitLab triggers, or ``dd-octo-sts`` scopes must either reuse a
variable from ``.gitlab/external-refs.yml`` or be allowlisted in
``.gitlab/external-refs.allowlist.txt`` with a justification.

Run from the repository root::

    python scripts/check-external-refs.py

The script exits with code 1 if any unallowlisted external reference is
found. It is wired into the GitLab CI pipeline as the ``external-refs-lint``
job.

This is a stdlib-only script — no external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent

# Files (and globs) we scan for external references.
SCAN_GLOBS: tuple[str, ...] = (
    ".gitlab-ci.yml",
    ".gitlab/**/*.yml",
    ".gitlab/**/*.sh",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".github/actions/**/*.yml",
    ".github/actions/**/*.yaml",
    "scripts/codeql_scan.sh",
)

# Files that are exempt entirely from the scan because they exist by design
# to enumerate or define external references.
EXEMPT_FILES: frozenset[str] = frozenset(
    {
        ".gitlab/external-refs.yml",
        ".gitlab/external-refs.allowlist.txt",
    }
)

# Glob prefixes whose entire contents are exempt (chainguard STS policies
# explicitly bind GitLab/GitHub paths and are themselves the centralization).
EXEMPT_PREFIXES: tuple[str, ...] = (".github/chainguard/",)


@dataclass(frozen=True)
class Pattern:
    """A pattern indicating an external reference that must be centralized."""

    name: str
    regex: re.Pattern[str]
    hint: str


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        name="gitlab-trigger-project",
        regex=re.compile(r"\bproject:\s*[\"']?DataDog/"),
        hint="Replace with $EXT_GITLAB_<NAME> from .gitlab/external-refs.yml.",
    ),
    Pattern(
        name="dd-octo-sts-scope",
        regex=re.compile(r"--scope\s+DataDog/"),
        hint="Replace with $EXT_GITHUB_<NAME> from .gitlab/external-refs.yml.",
    ),
    Pattern(
        name="internal-gitlab-host",
        regex=re.compile(r"gitlab\.ddbuild\.io/DataDog/"),
        hint="Replace with $EXT_HOST_INTERNAL_GITLAB/DataDog/ or allowlist with justification.",
    ),
    Pattern(
        name="github-action-uses",
        regex=re.compile(r"^\s*-?\s*uses:\s*DataDog/"),
        hint="Replace with a wrapper composite action under .github/actions/.",
    ),
    Pattern(
        name="checkout-repository",
        regex=re.compile(r"^\s*-?\s*repository:\s*[\"']?DataDog/"),
        hint="Allowlist with justification (most cases are OSS repos).",
    ),
    Pattern(
        name="git-clone-datadog",
        regex=re.compile(r"git\s+clone\s+(?:--\S+\s+)*[\"']?https://github\.com/DataDog/"),
        hint="Allowlist with justification (most cases are OSS or rerouted clones).",
    ),
)


@dataclass(frozen=True)
class Violation:
    file: str
    line_no: int
    line_text: str
    pattern_name: str
    hint: str


def _parse_allowlist(path: Path) -> set[str]:
    """Parse the allowlist file into a set of ``<file>:<line>`` keys.

    A key of ``<file>:*`` whitelists every line in the file.
    """
    if not path.exists():
        return set()

    entries: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Take the first token (everything up to the first whitespace).
        key, _sep, _rest = line.partition(" ")
        entries.add(key)
    return entries


def _is_allowlisted(allowlist: set[str], rel: str, line_no: int) -> bool:
    return f"{rel}:{line_no}" in allowlist or f"{rel}:*" in allowlist


def _is_exempt(rel: str) -> bool:
    if rel in EXEMPT_FILES:
        return True
    return any(rel.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def _collect_files() -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in SCAN_GLOBS:
        for p in ROOT.glob(pattern):
            if not p.is_file():
                continue
            if p in seen:
                continue
            seen.add(p)
            rel = str(p.relative_to(ROOT))
            if _is_exempt(rel):
                continue
            files.append(p)
    return sorted(files)


def _scan_file(path: Path) -> Iterable[Violation]:
    rel = str(path.relative_to(ROOT))
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in PATTERNS:
            if pattern.regex.search(line):
                yield Violation(
                    file=rel,
                    line_no=line_no,
                    line_text=line.strip(),
                    pattern_name=pattern.name,
                    hint=pattern.hint,
                )
                # One violation per line is enough to report.
                break


def main() -> int:
    allowlist = _parse_allowlist(ROOT / ".gitlab/external-refs.allowlist.txt")
    files = _collect_files()

    violations: list[Violation] = []
    for path in files:
        for v in _scan_file(path):
            if _is_allowlisted(allowlist, v.file, v.line_no):
                continue
            violations.append(v)

    if violations:
        print(
            f"Found {len(violations)} unallowlisted external reference(s):\n",
            file=sys.stderr,
        )
        for v in violations:
            preview = v.line_text if len(v.line_text) <= 100 else v.line_text[:97] + "..."
            print(f"  {v.file}:{v.line_no} [{v.pattern_name}]", file=sys.stderr)
            print(f"    {preview}", file=sys.stderr)
            print(f"    -> {v.hint}", file=sys.stderr)
            print(
                f"    (or add `{v.file}:{v.line_no} <justification>` to .gitlab/external-refs.allowlist.txt)",
                file=sys.stderr,
            )
            print("", file=sys.stderr)
        print(
            "See docs/github-org-migration-audit.md for context on the GitHub organization split (APMLP-1185).",
            file=sys.stderr,
        )
        return 1

    print(f"OK: scanned {len(files)} file(s), no unallowlisted external references found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
