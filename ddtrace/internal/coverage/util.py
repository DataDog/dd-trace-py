import functools
import os
import posixpath
import stat
import sys
from typing import Optional

from ddtrace.internal.coverage import native_lines
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


# The import hook resolves the file of every module imported while tests run, often inside a running
# event loop. By then the application may have patched the functions of os and os.path: blocking-call
# detectors such as blockbuster raise from them, fake file systems answer for files that are not there.
# Resolution therefore calls the posix module directly, as importlib does: tools patch os, not posix.
_posix = sys.modules.get("posix") if os.name == "posix" else None


def _is_link(path: str) -> bool:
    try:
        return stat.S_ISLNK(_posix.lstat(path).st_mode)  # type: ignore[union-attr]
    except (OSError, ValueError):
        return False


def _join_real(path: str, rest: str, seen: dict[str, Optional[str]]) -> tuple[str, bool]:
    # posixpath._joinrealpath of CPython 3.12, not strict, on the unpatched functions.
    if rest.startswith("/"):
        rest = rest[1:]
        path = "/"
    while rest:
        name, _, rest = rest.partition("/")
        if not name or name == ".":
            continue
        if name == "..":
            if path:
                path, name = posixpath.split(path)
                if name == "..":
                    path = posixpath.join(path, "..", "..")
            else:
                path = ".."
            continue
        newpath = posixpath.join(path, name)
        if not _is_link(newpath):
            path = newpath
            continue
        if newpath in seen:
            cached = seen[newpath]
            if cached is not None:
                path = cached
                continue
            # A symlink loop: the resolved part, then the rest unchanged.
            return posixpath.join(newpath, rest), False
        seen[newpath] = None
        try:
            target = _posix.readlink(newpath)  # type: ignore[union-attr]
        except OSError:
            # Gone since the lstat: resolve as if it were not a link.
            seen.pop(newpath)
            path = newpath
            continue
        path, ok = _join_real(path, target, seen)
        if not ok:
            return posixpath.join(path, rest), False
        seen[newpath] = path
    return path, True


def _realpath(path_str: str) -> str:
    if _posix is None:
        return os.path.realpath(path_str)
    path, _ = _join_real("", path_str, {})
    if not path.startswith("/"):
        path = posixpath.join(_posix.getcwd(), path)
    return posixpath.normpath(path)


@functools.lru_cache(maxsize=4096)
def resolved_path(path_str: str) -> str:
    # Memoized realpath for coverage hot paths: instrument_module runs for every
    # code object and every coverage report resolves the same workspace per test,
    # while the symlink layout never changes mid-run. Profiling showed thousands
    # of realpath/resolve calls on repeated inputs per run; the cache turns those
    # into dict lookups.
    directory, name = os.path.split(path_str)
    if directory and name and name not in (os.curdir, os.pardir):
        # Files of one directory share its resolution, so a new file costs one lstat
        # instead of one per path component.
        resolved = os.path.join(_resolved_directory(directory), name)
        if _posix is None:
            return os.path.realpath(resolved) if os.path.islink(resolved) else resolved
        return _realpath(resolved) if _is_link(resolved) else resolved
    return _realpath(path_str)


@functools.lru_cache(maxsize=1024)
def _resolved_directory(path_str: str) -> str:
    return _realpath(path_str)


def is_file_level_coverage_enabled() -> bool:
    # Python 3.12+ instruments with sys.monitoring. Native line collection reports lines where it can run; elsewhere
    # file-level coverage (PY_START events) is the default and avoids the cost of LINE events in Python callbacks.
    # Older versions default to line-level coverage.
    if native_lines.requested():
        return False
    default = "true" if sys.version_info >= (3, 12) else "false"
    return asbool(env.get("_DD_COVERAGE_FILE_LEVEL", default))


def collapse_ranges(numbers: list[int]) -> list[tuple[int, int]]:
    # This function turns an ordered list of numbers into a list of ranges.
    # For example, [1, 2, 3, 5, 6, 7, 9] becomes [(1, 3), (5, 7), (9, 9)]
    if not numbers:
        return []
    ranges = []
    start = end = numbers[0]
    for number in numbers[1:]:
        if number == end + 1:
            end = number
        else:
            ranges.append((start, end))
            start = end = number

    ranges.append((start, end))

    return ranges
