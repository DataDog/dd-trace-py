#!/usr/bin/env python3
"""Compare the vendored datadogpy version with the latest release on PyPI.

Outputs GitHub Actions step outputs:
  outdated       - "true" if PyPI is ahead of the vendored version, "false" otherwise
  latest_version - latest version string from PyPI
  vendored_version - version string parsed from ddtrace/vendor/__init__.py

Exit codes:
  0 - success (regardless of whether outdated)
  1 - unexpected error (e.g. PyPI unreachable, version not parseable)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


PYPI_URL = "https://pypi.org/pypi/datadog/json"
VENDOR_INIT = Path(__file__).parent.parent.parent / "ddtrace" / "vendor" / "__init__.py"


def _set_output(name: str, value: str) -> None:
    """Write a GitHub Actions step output."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"{name}={value}\n")
    else:
        # Running locally — just print
        print(f"  {name}={value}")


def _latest_pypi_version() -> str:
    try:
        req = urllib.request.Request(PYPI_URL)
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            data: dict[str, dict[str, str]] = json.load(resp)
        version: str = data["info"]["version"]
        return version
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: could not fetch latest datadogpy version from PyPI: {exc}", file=sys.stderr)
        sys.exit(1)


def _vendored_version() -> str:
    try:
        content = VENDOR_INIT.read_text()
    except FileNotFoundError:
        print(f"ERROR: {VENDOR_INIT} not found", file=sys.stderr)
        sys.exit(1)

    # Match the "Version: X.Y.Z" line inside the dogstatsd section
    match = re.search(
        r"dogstatsd\b.*?Version:\s*(\d+\.\d+(?:\.\d+)?)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        print(f"ERROR: could not parse vendored datadogpy version from {VENDOR_INIT}", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def main() -> None:
    latest = _latest_pypi_version()
    vendored = _vendored_version()

    print(f"Vendored datadogpy version : {vendored}")
    print(f"Latest PyPI version        : {latest}")

    outdated = latest != vendored
    if outdated:
        print("⚠ Vendored version is behind PyPI — consider a vendor bump.")
    else:
        print("✓ Vendored version is up to date.")

    _set_output("outdated", str(outdated).lower())
    _set_output("latest_version", latest)
    _set_output("vendored_version", vendored)


if __name__ == "__main__":
    main()
