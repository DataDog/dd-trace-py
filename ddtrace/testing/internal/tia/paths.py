# Adapted from pytest-filemon (Apache-2.0).
"""Path helpers."""

from __future__ import annotations

import os
from pathlib import Path


# Path segments that never hold first-party repository code: virtual
# environments and third-party packages that happen to live *inside* the
# repository tree (uv's default in-repo .venv, tox envs, vendored deps). A
# repo directory legitimately named like one of these is an accepted loss.
_EXCLUDED_SEGMENTS = frozenset(
    {
        "site-packages",
        "dist-packages",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        ".nox",
    }
)


def _compute_repo_rel(filename: str, root: Path) -> str | None:
    """Uncached core of :func:`to_repo_rel`."""
    try:
        p = Path(filename)
        if not p.is_absolute():
            p = Path.cwd() / p
        p = p.resolve()
        # Guard against phantom relative co_filenames (e.g. bare
        # "bootstrap.py" from some generated/embedded code): only real files
        # can be dependencies.
        if not p.is_file():
            return None
        rel = p.relative_to(root)
    except (OSError, ValueError, RuntimeError):
        return None
    parts = rel.parts
    if not parts or ".." in parts:
        return None
    if _EXCLUDED_SEGMENTS.intersection(parts):
        return None
    return rel.as_posix()


def to_repo_rel(filename: str | os.PathLike[str] | None, root: Path) -> str | None:
    """Normalize a filename to a repo-root-relative POSIX path.

    Returns None for synthetic locations (`<string>`, `<frozen ...>`),
    non-strings, paths outside the repository, and paths inside virtual
    environments / site-packages / similar third-party trees.

    Deliberately a pure function with no module-level cache: the monitor
    memoizes per instance (see ImportMonitor.repo_rel), so concurrent or
    nested monitors in one process never share (or stale-share) state.
    """
    if not filename:
        return None
    if not isinstance(filename, str):
        try:
            filename = os.fspath(filename)
        except TypeError:
            return None
    if not isinstance(filename, str) or not filename or filename.startswith("<"):
        return None
    return _compute_repo_rel(filename, root)
