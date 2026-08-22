#!/usr/bin/env python3
"""Validate that external references in CI files are centralized.

This script enforces the policy described in
``docs/github-org-migration-audit.md`` and ``.gitlab/external-refs.yml``:
new references to internal Datadog GitHub repos, ``*.ddbuild.io`` endpoints,
cross-project GitLab triggers, or ``dd-octo-sts`` scopes must either reuse a
variable from ``.gitlab/external-refs.yml`` or be allowlisted in
``.gitlab/external-refs.allowlist.txt`` with a justification.

It additionally enforces that wrapper composite actions under
``.github/actions/<name>/action.yml`` SHA-pin every ``uses:`` they delegate
to (40-character hex). That positive assertion is what prevents future
regressions inside the wrappers (e.g. swapping a SHA for ``@main``).

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
# AIDEV-NOTE: keep this list synced with the scope described in
# docs/github-org-migration-audit.md ("How to use this document").
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
        # services.yml is a pure registry of service-container images. Every
        # non-trivial line is `name: registry.ddbuild.io/...`. Enumerating each
        # line in the allowlist would obscure the policy, not enforce it.
        ".gitlab/services.yml",
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


# Global patterns: applied to every scanned file EXCEPT wrapper composite
# actions (which have a separate, stricter rule below).
#
# All regexes are compiled with re.IGNORECASE so that lowercase variants
# (`uses: datadog/…`, `--scope datadog/…`) — which resolve at runtime on
# GitHub but would otherwise evade the lint — are also caught.
GLOBAL_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        name="gitlab-trigger-project",
        regex=re.compile(r"\bproject:\s*[\"']?DataDog/", re.IGNORECASE),
        hint="Replace with $EXT_GITLAB_<NAME> from .gitlab/external-refs.yml.",
    ),
    Pattern(
        name="dd-octo-sts-scope",
        regex=re.compile(r"--scope\s+DataDog/", re.IGNORECASE),
        hint="Replace with $EXT_GITHUB_<NAME> from .gitlab/external-refs.yml.",
    ),
    Pattern(
        name="internal-gitlab-host",
        regex=re.compile(r"gitlab\.ddbuild\.io/DataDog/", re.IGNORECASE),
        hint="Replace with $EXT_HOST_INTERNAL_GITLAB/DataDog/ or allowlist with justification.",
    ),
    Pattern(
        name="internal-registry-host",
        regex=re.compile(r"registry\.ddbuild\.io/", re.IGNORECASE),
        hint=(
            "New `registry.ddbuild.io/...` image: document it in "
            "docs/github-org-migration-audit.md and allowlist with justification."
        ),
    ),
    Pattern(
        name="github-action-uses",
        regex=re.compile(r"^\s*-?\s*uses:\s*DataDog/", re.IGNORECASE),
        hint="Replace with a wrapper composite action under .github/actions/.",
    ),
    Pattern(
        name="checkout-repository",
        regex=re.compile(r"^\s*-?\s*repository:\s*[\"']?DataDog/", re.IGNORECASE),
        hint="Allowlist with justification (most cases are OSS repos).",
    ),
    Pattern(
        name="git-clone-datadog",
        regex=re.compile(
            r"git\s+clone\s+(?:--\S+\s+)*[\"']?https://github\.com/DataDog/",
            re.IGNORECASE,
        ),
        hint="Allowlist with justification (most cases are OSS or rerouted clones).",
    ),
)

# Wrapper-action patterns: applied ONLY to composite actions under
# `.github/actions/<name>/action.yml`. The whole point of those wrappers is
# to centralize `uses: DataDog/<action>@<sha>` lines, so the global
# `github-action-uses` rule does not apply. Instead, we require that every
# `uses:` line in a wrapper SHA-pins its reference (40 hex chars), which is
# the security property the wrappers exist to guarantee.
#
# The negative lookahead matches anything after `@` that is NOT a 40-char
# hex SHA followed by EOL / whitespace / inline `#` comment. So:
#   uses: DataDog/foo@96a25462dbcb10ebf0bfd6e2ccc917d2ab235b9a # v1.0.4  -> OK
#   uses: DataDog/foo@v1.0.4                                              -> violation
#   uses: DataDog/foo@main                                                -> violation
WRAPPER_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        name="wrapper-uses-not-sha-pinned",
        regex=re.compile(
            r"^\s*-?\s*uses:\s*[\w./-]+@(?![a-f0-9]{40}(?:\s|#|$))",
            re.IGNORECASE,
        ),
        hint=(
            "Wrapper composite actions MUST SHA-pin every `uses:` reference "
            "(40 hex chars). Tag/branch pins defeat the purpose of the wrapper."
        ),
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


def _is_wrapper_action(rel: str) -> bool:
    """True if this file is a composite-action wrapper under .github/actions/."""
    return rel.startswith(".github/actions/") and rel.endswith(("/action.yml", "/action.yaml"))


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
    patterns = WRAPPER_PATTERNS if _is_wrapper_action(rel) else GLOBAL_PATTERNS
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in patterns:
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
