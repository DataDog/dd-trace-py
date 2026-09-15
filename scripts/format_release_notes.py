#!/usr/bin/env python3
"""Reformat raw reno release notes into grouped, per-category bullet lists.

Reads from stdin the exact output of:

    reno report | pandoc -f rst -t gfm --wrap=none

which looks like:

    # Release Notes

    ## Unreleased

    ### New Features

    - tracing: Adds support for X.

    <!-- -->

    - tracing: Adds support for Y.

    ## v1.2.3

    ### New Features

    - tracing: Adds support for Z.

Notes are grouped by their leading "Category: " prefix within each
"### Section" and rendered as a heading with indented sub-bullets:

    ### New Features

    - tracing
      - Adds support for X.
      - Adds support for Y.

Usage:
    reno report | pandoc -f rst -t gfm --wrap=none | python scripts/format_release_notes.py
    reno report | pandoc -f rst -t gfm --wrap=none | python scripts/format_release_notes.py v1.2.3
    python scripts/format_release_notes.py v1.2.3 notes.gfm.md
    python scripts/format_release_notes.py  # no stdin, no input_file: runs reno/pandoc itself
"""

import argparse
import re
import subprocess
import sys

from packaging.version import InvalidVersion
from packaging.version import Version


SECTION_RE = re.compile(r"(?m)^(### .+)$")
VERSION_RE = re.compile(r"(?m)^## (.+)$")
SEPARATOR_RE = re.compile(r"\n?<!-- -->\n?")
CATEGORY_RE = re.compile(r"^([^:]{1,80}?):\s+(.*)$", re.DOTALL)

UNRELEASED = "Unreleased"


def parse_entries(body):
    """Split a section body into (category_or_None, text) tuples, in order.

    Only the leading "Category: " line is stripped/matched; everything after
    it is kept as-is (minus the 2-space list-nesting indent pandoc adds) so
    nested sub-lists, code blocks, and multiple paragraphs survive intact.
    """
    entries = []
    for block in SEPARATOR_RE.split(body):
        block = block.strip("\n")
        if not block.strip():
            continue
        lines = block.split("\n")
        first_line = re.sub(r"^-\s*", "", lines[0].strip())
        rest = [re.sub(r"^  ", "", line) for line in lines[1:]]
        match = CATEGORY_RE.match(first_line)
        if match:
            category, head = match.group(1).strip(), match.group(2).strip()
        else:
            category, head = None, first_line
        text = "\n".join([head] + rest).rstrip("\n")
        entries.append((category, text))
    return entries


def render_entry(text, indent):
    """Render a (possibly multi-line) entry as a "- " bullet at indent,
    reindenting continuation lines so nested Markdown stays valid.
    """
    text_lines = text.split("\n")
    out = [f"{indent}- {text_lines[0]}"]
    cont_indent = indent + "  "
    for line in text_lines[1:]:
        out.append(f"{cont_indent}{line}" if line.strip() else "")
    return out


def format_section_body(body):
    groups = {}
    labels = {}
    order = []
    uncategorized = []
    for category, text in parse_entries(body):
        if category is None:
            uncategorized.append(text)
            continue
        key = category.lower()
        if key not in groups:
            groups[key] = []
            labels[key] = category
            order.append(key)
        groups[key].append(text)

    lines = []
    for key in order:
        lines.append(f"- {labels[key]}")
        for text in groups[key]:
            lines.extend(render_entry(text, "  "))
    for text in uncategorized:
        lines.extend(render_entry(text, ""))
    return "\n".join(lines)


def format_release_body(body):
    """Format a single release's body (everything under a "## <version>" heading)."""
    parts = SECTION_RE.split(body)
    preamble = parts[0].strip("\n")

    out = []
    if preamble:
        out.append(preamble)

    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        section_body = parts[i + 1] if i + 1 < len(parts) else ""
        formatted_body = format_section_body(section_body)
        section = header if not formatted_body else f"{header}\n\n{formatted_body}"
        out.append(section)

    return "\n\n".join(out)


def parse_releases(raw_text):
    """Split raw pandoc-converted reno output into an ordered list of (version, body)."""
    parts = VERSION_RE.split(raw_text)
    # parts[0] is the "# Release Notes" preamble, discarded: release notes are
    # only ever consumed per-version.
    releases = []
    for i in range(1, len(parts), 2):
        version = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        releases.append((version, body))
    return releases


def normalize_version(version):
    return version[1:] if version.startswith("v") else version


def parsed_version(version):
    try:
        return Version(normalize_version(version))
    except InvalidVersion:
        return None


def select_release_body(releases, earliest_version):
    """Pick the body to format for earliest_version, per the module docstring rules."""
    for version, body in releases:
        if version == earliest_version or normalize_version(version) == normalize_version(earliest_version):
            return body

    target = parsed_version(earliest_version)
    if target is None:
        raise ValueError(f"version {earliest_version!r} not found and is not a valid semantic version")

    known_versions = [v for v, _ in releases if v != UNRELEASED]
    latest = max((v for v in (parsed_version(v) for v in known_versions) if v is not None), default=None)

    if latest is not None and target <= latest:
        raise ValueError(f"version {earliest_version!r} not found among known releases: {known_versions}")

    for version, body in releases:
        if version == UNRELEASED:
            return body

    raise ValueError(f"version {earliest_version!r} not found and no {UNRELEASED!r} section is present")


def format_release_notes(raw_text, earliest_version=None):
    releases = parse_releases(raw_text)

    if earliest_version is not None:
        body = select_release_body(releases, earliest_version)
        return format_release_body(body) + "\n"

    out = []
    for version, body in releases:
        formatted_body = format_release_body(body)
        if formatted_body:
            out.append(f"## {version}\n\n{formatted_body}")
    return "\n\n".join(out) + "\n"


def generate_raw_text():
    """Run "reno report | pandoc -f rst -t gfm --wrap=none" and return its output."""
    reno = subprocess.run(["reno", "report"], capture_output=True, text=True, check=True)
    pandoc = subprocess.run(
        ["pandoc", "-f", "rst", "-t", "gfm", "--wrap=none"],
        input=reno.stdout,
        capture_output=True,
        text=True,
        check=True,
    )
    return pandoc.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("earliest_version", nargs="?", default=None)
    parser.add_argument("input_file", nargs="?", default=None)
    args = parser.parse_args()

    if args.input_file is not None:
        with open(args.input_file, "r") as f:
            raw_text = f.read()
    elif not sys.stdin.isatty():
        raw_text = sys.stdin.read()
        if not raw_text:
            # Non-interactive stdin (CI, IDE, cron, /dev/null) with nothing
            # actually piped in reads as an empty string, not a TTY. Treat
            # it the same as no stdin at all.
            raw_text = generate_raw_text()
    else:
        raw_text = generate_raw_text()

    result = format_release_notes(raw_text, args.earliest_version)
    sys.stdout.write(result)


if __name__ == "__main__":
    main()
