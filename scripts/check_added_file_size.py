#!/usr/bin/env python3
"""Fail CI if a pull request adds a file larger than the repo's p99 file size.

The threshold below was computed once, offline, from every file tracked by
git in this repository. Recompute it with::

    git ls-files -z | xargs -0 du -b | awk '{print $1}' | python3 -c "
        import sys
        import numpy as np
        sizes = [int(line) for line in sys.stdin]
        print('p99 size of', len(sizes), 'files:', np.percentile(sizes, 99, method='higher'), 'bytes')
    "

Usage:

    python scripts/check_added_file_size.py [--base-ref REF]

--base-ref defaults to the pull request's base ref (read from
GITHUB_EVENT_PATH/GITHUB_BASE_REF), falling back to origin/HEAD.

To intentionally add a large file, add the 'large-file-ok' label to the
pull request; this skips the check.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Optional


# p99 size (in bytes) of all files tracked by git was 55KiB.
# See module docstring for how this was computed.
MAX_FILE_SIZE_BYTES = 100_000

# Label maintainers/contributors can add to a pull request to skip this check
# for an intentional large file addition.
SKIP_LABEL = "large-file-ok"


def _pr_labels() -> list[str]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not pathlib.Path(event_path).is_file():
        return []

    event = json.loads(pathlib.Path(event_path).read_text())
    labels = (event.get("pull_request") or {}).get("labels") or []
    return [label["name"] for label in labels if "name" in label]


def _resolve_base_ref(explicit_base_ref: Optional[str]) -> str:
    if explicit_base_ref:
        return explicit_base_ref

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and pathlib.Path(event_path).is_file():
        event = json.loads(pathlib.Path(event_path).read_text())
        base_ref = (event.get("pull_request") or {}).get("base", {}).get("ref")
        if base_ref:
            return base_ref if base_ref.startswith("origin/") else f"origin/{base_ref}"

    github_base_ref = os.environ.get("GITHUB_BASE_REF")
    if github_base_ref:
        return f"origin/{github_base_ref}"

    return "origin/HEAD"


def _added_files(base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", base_ref],
        capture_output=True,
        check=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", default=None, help="Git ref to diff against. Defaults to the PR base ref.")
    args = parser.parse_args(argv)

    if SKIP_LABEL in _pr_labels():
        print(f"OK: skipping check, pull request has the '{SKIP_LABEL}' label.")
        return 0

    base_ref = _resolve_base_ref(args.base_ref)
    print(f"Comparing against base ref: {base_ref}")

    try:
        added_files = _added_files(base_ref)
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute diff against {base_ref}: {exc.stderr}", file=sys.stderr)
        return 1

    violations: list[tuple[str, int]] = []
    for file_path in added_files:
        path = pathlib.Path(file_path)
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_FILE_SIZE_BYTES:
            violations.append((file_path, size))

    if not violations:
        print(f"OK: no added file exceeds the repo's p99 file size ({MAX_FILE_SIZE_BYTES} bytes).")
        return 0

    violations.sort(key=lambda item: -item[1])
    print(
        f"ERROR: {len(violations)} added file(s) exceed the repo's p99 file size ({MAX_FILE_SIZE_BYTES} bytes):",
        file=sys.stderr,
    )
    for file_path, size in violations:
        print(f"  - {file_path}: {size} bytes", file=sys.stderr)
    print(
        f"\nIf this file genuinely needs to be this large, add the '{SKIP_LABEL}' "
        "label to the pull request to skip this check, or reduce its size "
        "(compress it, trim unused data, or store it outside the repo).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
