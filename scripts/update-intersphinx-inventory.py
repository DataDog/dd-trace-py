#!/usr/bin/env python3
"""Refresh or check the offline fallback for the Python intersphinx inventory.

docs/conf.py falls back to docs/_intersphinx/python3.inv when docs.python.org is
unreachable, so a python.org outage does not fail the docs build. Only the py:
domain is kept: it is the only part of the inventory our docs resolve against,
and the full file is over the repository's added-file size limit.

Usage:

    python scripts/update-intersphinx-inventory.py          # rewrite the fallback
    python scripts/update-intersphinx-inventory.py --check  # exit 1 if it is stale

In check mode a failure to fetch or parse the upstream inventory is reported but
exits 0, since the nightly job that runs it will try again the next night.
"""

import argparse
import http.client
from pathlib import Path
import re
import sys
import urllib.request
import zlib


SOURCE_URL = "https://docs.python.org/3/objects.inv"
DESTINATION = Path(__file__).resolve().parent.parent / "docs" / "_intersphinx" / "python3.inv"
HEADER_LINES = 4
# Same entry format Sphinx itself parses: name, domain:role, priority, uri, display name.
ENTRY = re.compile(r"(.+?)\s+(\S+)\s+(-?\d+)\s+?(\S*)\s+(.*)")


class InventoryError(Exception):
    pass


def parse(data):
    """Return the header lines and the sorted py: domain entry lines of an inventory."""
    parts = data.split(b"\n", HEADER_LINES)
    if len(parts) <= HEADER_LINES or parts[0] != b"# Sphinx inventory version 2":
        raise InventoryError("not a version 2 Sphinx inventory")
    header = [line.decode("utf-8") for line in parts[:HEADER_LINES]]
    try:
        body = zlib.decompress(parts[HEADER_LINES]).decode("utf-8")
    except (zlib.error, UnicodeDecodeError) as exc:
        raise InventoryError(f"cannot decompress inventory body: {exc}") from exc

    entries = []
    for line in body.splitlines():
        match = ENTRY.fullmatch(line.rstrip())
        if match is not None and match.group(2).startswith("py:"):
            entries.append(line.rstrip())
    if not entries:
        raise InventoryError("inventory has no py: domain entries")
    return header, sorted(set(entries))


def serialize(header, entries):
    body = zlib.compress(("\n".join(entries) + "\n").encode("utf-8"), 9)
    return ("\n".join(header) + "\n").encode("utf-8") + body


def keys(entries):
    # The role and name decide whether a reference resolves and the URI decides where it
    # links to; priority and display name do not affect the generated links.
    matches = (ENTRY.fullmatch(line) for line in entries)
    return {(match.group(2), match.group(1), match.group(4)) for match in matches if match is not None}


def fetch():
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:  # nosec: B310
        return response.read()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Only report whether the fallback is up to date.")
    args = parser.parse_args(argv)

    try:
        header, entries = parse(fetch())
    # HTTPException covers responses cut short mid-body (IncompleteRead), which are not OSErrors.
    except (OSError, http.client.HTTPException, InventoryError) as exc:
        print(f"warning: could not get {SOURCE_URL}: {exc}", file=sys.stderr)
        return 0 if args.check else 1

    if not args.check:
        DESTINATION.parent.mkdir(parents=True, exist_ok=True)
        DESTINATION.write_bytes(serialize(header, entries))
        print(f"Wrote {len(entries)} entries to {DESTINATION}")
        return 0

    _, current_entries = parse(DESTINATION.read_bytes())
    upstream, current = keys(entries), keys(current_entries)
    if upstream == current:
        print(f"{DESTINATION} is up to date ({len(current)} entries).")
        return 0

    print(
        f"{DESTINATION} is out of date: {len(upstream - current)} entries added or moved upstream, "
        f"{len(current - upstream)} removed or moved. Run `python scripts/update-intersphinx-inventory.py` "
        "and commit the result.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
