#!/usr/bin/env python3
"""Rewrite every symlink under a directory as a relative symlink.

Usage: make-symlinks-relative.py [ROOT]

rdfind writes absolute symlinks, and absolute targets break when the OCI package
is extracted at a different path. Each new target is the fully resolved target
of the link, relative to the resolved directory of the link. This is the result
of `realpath --relative-to="$(dirname "$(realpath -s LINK)")" "$(realpath LINK)"`.

The script checks every link before it changes any of them. A broken link or a
link to a directory makes the script exit with status 1 and change nothing.
"""

import os
import sys


def _find_symlinks(root):
    """Yield every symlink under root without following symlinks to directories."""
    stack = [root]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                if entry.is_symlink():
                    yield entry.path
                elif entry.is_dir():
                    stack.append(entry.path)


def _plan(root):
    """Return (link, new_target) pairs, or exit if a link cannot be made relative."""
    resolved_dirs = {}
    plan = []
    errors = []
    for link in _find_symlinks(root):
        link_dir = os.path.dirname(link)
        if link_dir not in resolved_dirs:
            resolved_dirs[link_dir] = os.path.realpath(link_dir)
        try:
            target = os.path.realpath(link, strict=True)
        except OSError as e:
            errors.append(f"{link}: cannot resolve target: {e}")
            continue
        if os.path.isdir(target):
            errors.append(f"{link}: target is a directory: {target}")
            continue
        plan.append((link, os.path.relpath(target, resolved_dirs[link_dir])))
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        sys.exit(1)
    return plan


def main(argv):
    if len(argv) > 2:
        sys.stderr.write(__doc__)
        return 2
    root = argv[1] if len(argv) == 2 else "."
    for link, new_target in _plan(root):
        if os.readlink(link) == new_target:
            continue
        # Replace the link with a rename so that a failure never leaves the path missing.
        tmp = link + ".relative-tmp"
        os.symlink(new_target, tmp)
        os.replace(tmp, link)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
