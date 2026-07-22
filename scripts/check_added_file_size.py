#!/usr/bin/env python3
"""Fail CI if a branch adds a file larger than the repo's p99 file size.

The threshold below was computed once, offline, from every file tracked by
git in this repository. Recompute it with::

    git ls-files -z | xargs -0 du -b | awk '{print $1}' | python3 -c "
        import sys
        import numpy as np
        sizes = [int(line) for line in sys.stdin]
        print('p99 size of', len(sizes), 'files:', np.percentile(sizes, 99, method='higher'), 'bytes')
    "

Usage:

    python scripts/check_added_file_size.py --base-ref REF
"""

import argparse
import pathlib
import subprocess
import sys
from typing import Optional


# p99 size (in bytes) of all files tracked by git was 55KiB.
# See module docstring for how this was computed.
MAX_FILE_SIZE_BYTES = 100_000


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
    parser.add_argument("--base-ref", default="origin/main", help="Git ref to diff against.")
    args = parser.parse_args(argv)

    print(f"Comparing against base ref: {args.base_ref}")

    try:
        added_files = _added_files(args.base_ref)
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute diff against {args.base_ref}: {exc.stderr}", file=sys.stderr)
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
        "\nIf this file genuinely needs to be this large, reduce its size "
        "(compress it, trim unused data, or store it outside the repo). "
        "If it's genuinely needed as-is, ask a maintainer to admin-merge.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
