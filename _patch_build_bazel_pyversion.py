#!/usr/bin/env python3
"""Idempotently pin a Python version on a Rapid service's BUILD.bazel target.

Usage:
    _patch_build_bazel_pyversion.py <BUILD.bazel path> <target name> <python version>

Behavior:
- Locates the macro/rule call whose `name = "<target name>"` kwarg matches the
  provided target.
- If the call already has a `python_version = "X.Y"` kwarg, replaces it.
- If it does not, inserts `python_version = "<X.Y>",` as the first kwarg
  immediately after the opening `(` of the call.
- Idempotent: running with the same arguments on an already-patched file is a
  no-op (no write, exits 0).

Exits non-zero if it can't find a clear insertion point (e.g. the target name
doesn't appear, or appears inside a string literal we can't safely parse).
"""

from __future__ import annotations

import re
import sys


def _find_call_span(text: str, target_name: str) -> tuple[int, int] | None:
    """Locate the (start, end) byte span of the macro call whose `name =`
    kwarg equals ``target_name``.

    Strategy: find every line containing ``name = "<target>"`` (Starlark
    convention is one kwarg per line), then walk back to the previous line
    that opens a call (line ending in ``(``) and forward to the matching
    closing ``)``.
    """
    pattern = re.compile(
        r'^\s*name\s*=\s*"' + re.escape(target_name) + r'"\s*,?\s*$',
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return None

    # Walk back to the line ending in `(` — that's the macro/rule opener.
    name_line_start = text.rfind("\n", 0, match.start()) + 1
    cursor = name_line_start
    call_open = -1
    while cursor > 0:
        prev_line_end = text.rfind("\n", 0, cursor - 1)
        if prev_line_end < 0:
            break
        line_start = prev_line_end + 1
        line = text[line_start : cursor - 1]
        if line.rstrip().endswith("("):
            # call_open points at the `(` itself, not at the trailing newline.
            paren_offset = line.rfind("(")
            call_open = line_start + paren_offset
            break
        cursor = line_start
    if call_open < 0:
        return None

    # Walk forward, counting parens, to find the matching `)`.
    depth = 0
    i = call_open
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return (call_open, i + 1)
        i += 1
    return None


def _patch(text: str, target_name: str, py_version: str) -> tuple[str, bool]:
    """Return (new_text, changed). Idempotent."""
    span = _find_call_span(text, target_name)
    if span is None:
        raise SystemExit(f'ERROR: could not find macro/rule call with name = "{target_name}"')
    call_open, call_close = span
    call_body = text[call_open:call_close]

    # 1) Does the call already have a python_version kwarg?
    existing = re.search(
        r'(\bpython_version\s*=\s*)"([^"]*)"',
        call_body,
    )
    if existing:
        current = existing.group(2)
        if current == py_version:
            return text, False  # already pinned to the requested version
        new_body = call_body[: existing.start(2)] + py_version + call_body[existing.end(2) :]
        return text[:call_open] + new_body + text[call_close:], True

    # 2) Otherwise insert `python_version = "<X.Y>",` as the first kwarg.
    # call_body starts with whatever leads up to `(`; locate the `(` and
    # insert the new line right after it. Preserve the indentation of the
    # next non-empty line so the inserted line aligns visually.
    paren_idx = call_body.index("(")
    after_paren = call_body[paren_idx + 1 :]
    # Indent: take leading whitespace of the first non-empty line in `after_paren`.
    indent_match = re.match(r"\s*\n([ \t]+)", after_paren)
    indent = indent_match.group(1) if indent_match else "    "
    insertion = f'\n{indent}python_version = "{py_version}",'
    new_body = call_body[: paren_idx + 1] + insertion + after_paren
    return text[:call_open] + new_body + text[call_close:], True


def main() -> int:
    if len(sys.argv) != 4:
        sys.exit(__doc__.strip().splitlines()[2])
    build_bazel_path, target_name, py_version = sys.argv[1], sys.argv[2], sys.argv[3]

    if not re.fullmatch(r"3\.\d+", py_version):
        sys.exit(f"ERROR: python_version '{py_version}' must look like '3.12' or '3.9'")

    with open(build_bazel_path, "r", encoding="utf-8") as f:
        text = f.read()

    new_text, changed = _patch(text, target_name, py_version)

    if not changed:
        print(f"{build_bazel_path}: already pinned to python_version={py_version} (no-op)")
        return 0

    with open(build_bazel_path, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"{build_bazel_path}: pinned python_version={py_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
