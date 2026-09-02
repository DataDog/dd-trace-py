r"""Cheap replacements for the pathlib containment operations.

``PurePath.relative_to`` and ``PurePath.is_relative_to`` are the natural way to
ask "is this file inside that directory, and where relative to it", but on
Python 3.12 a single call costs path allocations and case-folded comparisons
proportional to the depth of *both* operands, whether it succeeds or fails. Code
that probes one file against every entry of ``sys.path`` then spends most of its
time building throwaway path objects, which is what ``ddtrace.internal.packages``
was doing in production allocation profiles.

These helpers answer the same questions by comparing ``PurePath.parts``, which
pathlib has already parsed: containment becomes a prefix check on a tuple of
strings, with no intermediate objects and no exceptions on the miss path.

Contract, and where it differs from pathlib:

- Arguments must be ``PurePath`` instances; a string raises ``AttributeError``
  rather than quietly answering. Comparing parsed components is what guarantees
  that ``.`` and repeated separators are already collapsed, and that anchors
  match only as whole units -- ``/`` and POSIX's separate ``//``, or Windows'
  ``C:`` and ``C:\``, are distinct, as pathlib also has it. (pathlib agreed on
  that last one only from 3.12, where anchors became strict; no caller here
  passes a non-absolute root.)
- ``..`` is not resolved and nothing here touches the filesystem, so resolve
  first if symlinks matter. Comparing a resolved path against an unresolved root
  is as meaningless here as it is in pathlib.
- Case sensitivity follows the host rather than the flavour of the path passed
  in. That matches pathlib for the native paths every caller uses, but a
  ``PureWindowsPath`` compared on POSIX will not fold case.
- A path is relative to itself, giving an empty tuple of components, matching
  the empty ``.parts`` of the ``"."`` pathlib returns.
"""

import os
from pathlib import PurePath
import typing as t


# Roots are given back to the caller unchanged, so a caller passing concrete
# Path objects keeps getting Path objects (and their I/O methods) back.
AnyPurePath = t.TypeVar("AnyPurePath", bound=PurePath)

# Module globals so the tests can force Windows rules on a POSIX host, which is
# the only coverage the Windows behaviour gets: CI here is POSIX-only.
_CASE_INSENSITIVE = os.path.normcase("A") != "A"
_SEP = os.sep


def _starts_with(path_parts: tuple[str, ...], root_parts: tuple[str, ...], n: int) -> bool:
    """Whether the first n components of path_parts are root_parts.

    Callers must have checked ``n == len(root_parts) <= len(path_parts)``, which
    is what bounds the zip below.

    Folding per component, rather than folding the joined path and indexing into
    it, because case folding does not preserve length: the lowercase of U+0130 is
    two characters, and an offset-based version silently truncated components.
    """
    if _CASE_INSENSITIVE:
        return all(a.lower() == b.lower() for a, b in zip(path_parts, root_parts))
    return path_parts[:n] == root_parts


def relative_parts(path: PurePath, root: PurePath) -> t.Optional[tuple[str, ...]]:
    """Components of path relative to root, or None if not contained.

    The fast equivalent of ``path.relative_to(root).parts`` guarded by a
    ``try/except ValueError``. Returns an empty tuple when path is root itself.
    """
    path_parts = path.parts
    root_parts = root.parts
    n = len(root_parts)

    if not n:
        # A root of "." has no components at all. pathlib makes only another
        # anchorless path relative to it, since the anchors have to agree.
        return None if path.anchor else path_parts

    if n > len(path_parts) or not _starts_with(path_parts, root_parts, n):
        return None

    return path_parts[n:]


def is_contained(path: PurePath, root: PurePath) -> bool:
    """Whether path is root or lives under it.

    The fast equivalent of ``path.is_relative_to(root)``. Prefer
    ``relative_parts`` when the relative components are needed too, so the
    containment test is not paid twice.
    """
    path_parts = path.parts
    root_parts = root.parts
    n = len(root_parts)

    if not n:
        return not path.anchor

    return n <= len(path_parts) and _starts_with(path_parts, root_parts, n)


def relative_path(path: PurePath, root: PurePath) -> t.Optional[str]:
    """path relative to root as a string, or None if not contained.

    The fast equivalent of ``str(path.relative_to(root))``, using the native
    separator. Yields ``""`` rather than pathlib's ``"."`` when path is root.
    """
    parts = relative_parts(path, root)
    return None if parts is None else _SEP.join(parts)


def deepest_containing_root(
    path: PurePath, roots: t.Iterable[AnyPurePath]
) -> t.Optional[tuple[AnyPurePath, tuple[str, ...]]]:
    """The most specific root containing path, with path relative to it.

    Returns the (root, relative_parts) pair for the deepest root that contains
    path, or None if none does. The root is returned as it was passed in, so
    callers keep working with their own path objects.

    Deepest root and shortest relative path are the same criterion, the path
    being fixed. It is the one that identifies which package a file belongs to:
    with a virtualenv and its site-packages both on sys.path, only the latter
    anchors the module name correctly.
    """
    path_parts = path.parts
    depth = len(path_parts)
    best: t.Optional[AnyPurePath] = None
    best_n = -1

    for root in roots:
        root_parts = root.parts
        n = len(root_parts)

        # Any two roots containing the same path are prefixes of one another, so
        # component count orders them by depth and a shallower one cannot win.
        if n <= best_n or n > depth:
            continue

        if not n:
            if not path.anchor and best is None:
                best, best_n = root, 0
            continue

        if not _starts_with(path_parts, root_parts, n):
            continue

        best, best_n = root, n

    return None if best is None else (best, path_parts[best_n:])
