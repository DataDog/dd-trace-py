#!/usr/bin/env python3
"""Compare the vendored datadogpy version with the latest release on PyPI.

Values written to ``$GITHUB_OUTPUT`` (GitHub Actions inter-step data):
  outdated         - ``"true"`` when PyPI is ahead of the vendored pin, ``"false"`` otherwise
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

    # Accept both formats:
    #   "Version: 0.52.1"          (clean semver, used after the vendor bump)
    #   "Version: 8e11af2 (0.39.1)"  (git-hash + semver in parens, used before)
    version_match: re.Match[str] | None = re.search(
        r"Version:.*?(\d+\.\d+\.\d+)",
        section,
    )
    if not version_match:
        print(f"ERROR: could not parse vendored datadogpy version from {VENDOR_INIT}", file=sys.stderr)
        sys.exit(1)
    return version_match.group(1)


def main() -> None:
    latest: str = _latest_pypi_version()
    vendored: str = _vendored_version()

    print(f"Vendored datadogpy version : {vendored}")
    print(f"Latest PyPI version        : {latest}")

    outdated: bool = latest != vendored
    if outdated:
        print("⚠ Vendored version is behind PyPI — consider a vendor bump.")
    else:
        print("✓ Vendored version is up to date.")

    _set_output("outdated", str(outdated).lower())
    _set_output("latest_version", latest)
    _set_output("vendored_version", vendored)


if __name__ == "__main__":
    main()
