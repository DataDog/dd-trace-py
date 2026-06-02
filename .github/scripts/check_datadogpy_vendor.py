#!/usr/bin/env python3
"""Compare the vendored datadogpy version with the latest release on PyPI.

Values written to ``$GITHUB_OUTPUT`` (GitHub Actions inter-step data):
  outdated         - ``"true"`` when the PyPI release is strictly newer than the vendored pin, ``"false"`` otherwise
  latest_version   - latest version string fetched from PyPI (e.g. ``"0.53.0"``)
  vendored_version - version string parsed from ``ddtrace/vendor/__init__.py`` (e.g. ``"0.52.1"``)

Exit codes:
  0 - completed successfully (regardless of whether the vendored copy is outdated)
  1 - unexpected error (e.g. PyPI unreachable, version string not parseable)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


# Use the copy of `packaging` that is already vendored in this repository so
# the workflow step works without a separate `pip install` and the dependency
# version is pinned alongside the rest of the codebase.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "ddtrace" / "vendor"))

from packaging.version import InvalidVersion  # noqa: E402
from packaging.version import Version  # noqa: E402


PYPI_URL: str = "https://pypi.org/pypi/datadog/json"
VENDOR_INIT: Path = Path(__file__).parent.parent.parent / "ddtrace" / "vendor" / "__init__.py"


def _set_output(name: str, value: str) -> None:
    """Append ``name=value`` to the GitHub Actions step-output file.

    When run locally (``$GITHUB_OUTPUT`` is unset) the value is printed to
    stdout instead so the output is still visible.
    """
    github_output: str | None = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"{name}={value}\n")
    else:
        print(f"  {name}={value}")


def _latest_pypi_version() -> str:
    try:
        req: urllib.request.Request = urllib.request.Request(PYPI_URL)
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            data: dict[str, dict[str, str]] = json.load(resp)
        version: str = data["info"]["version"]
        return version
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: could not fetch latest datadogpy version from PyPI: {exc}", file=sys.stderr)
        sys.exit(1)


def _vendored_version() -> str:
    try:
        content: str = VENDOR_INIT.read_text()
    except FileNotFoundError:
        print(f"ERROR: {VENDOR_INIT} not found", file=sys.stderr)
        sys.exit(1)

    # Extract just the dogstatsd section (from its header up to the next package header
    # or end of file) so we don't accidentally pick up a Version: line from another
    # vendored package.  Each section starts with "<name>\n---+\n"; the lookahead
    # matches that two-line opener to stop before the next section begins.
    section_match: re.Match[str] | None = re.search(
        r"^dogstatsd\n-+\n(.*?)(?=\n\w[^\n]*\n-+\n|\Z)",
        content,
        re.DOTALL | re.MULTILINE,
    )
    if not section_match:
        print(f"ERROR: dogstatsd section not found in {VENDOR_INIT}", file=sys.stderr)
        sys.exit(1)

    section: str = section_match.group(1)

    # Accept both formats, preserving any PEP 440 suffix (rc, post, dev, …):
    #   "Version: 0.52.1"              (clean release after the vendor bump)
    #   "Version: 0.52.1rc1"           (hypothetical pre-release pin)
    #   "Version: 8e11af2 (0.39.1)"    (git-hash + version in parens, legacy)
    # The character class [^\s)] stops at whitespace or a closing paren so the
    # parenthesised form is handled without a separate branch.
    version_match: re.Match[str] | None = re.search(
        r"Version:.*?(\d+\.\d+\.\d+[^\s)]*)",
        section,
    )
    if not version_match:
        print(f"ERROR: could not parse vendored datadogpy version from {VENDOR_INIT}", file=sys.stderr)
        sys.exit(1)
    return version_match.group(1)


def _is_outdated(vendored: str, latest: str) -> bool:
    """Return True when the PyPI release is strictly newer than the vendored pin.

    Uses ``packaging.version.Version`` for correct PEP 440 ordering so that
    post-releases, pre-releases, and dev releases all compare properly::

        "0.53.0.post1" > "0.53.0"   → True   (post-release is newer)
        "0.53.0rc1"    < "0.53.0"   → False  (pre-release is older)
        "0.44.1.dev0"  < "0.44.1"   → False  (dev release is older)
        "0.53.0"      == "0.53.0"   → False  (same)
    """
    try:
        return Version(latest) > Version(vendored)
    except InvalidVersion as exc:
        print(f"ERROR: cannot compare versions {vendored!r} and {latest!r}: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    latest: str = _latest_pypi_version()
    vendored: str = _vendored_version()

    print(f"Vendored datadogpy version : {vendored}")
    print(f"Latest PyPI version        : {latest}")

    outdated: bool = _is_outdated(vendored, latest)
    if outdated:
        print("⚠ Vendored version is behind PyPI — consider a vendor bump.")
    else:
        print("✓ Vendored version is up to date.")

    _set_output("outdated", str(outdated).lower())
    _set_output("latest_version", latest)
    _set_output("vendored_version", vendored)


if __name__ == "__main__":
    main()
