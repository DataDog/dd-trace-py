"""Helpers for profiler package metadata."""

import functools
import re
from typing import Any
from typing import Optional

from ddtrace import _build_metadata
from ddtrace.version import __version__


_FINAL_RELEASE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _normalize_git_sha(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    value = value.strip().lower()
    if 7 <= len(value) <= 64 and all(c in "0123456789abcdef" for c in value):
        return value

    return None


@functools.lru_cache(maxsize=1)
def get_profiler_git_commit_sha() -> Optional[str]:
    """Return the git commit SHA stamped into the installed ddtrace build, if available."""
    return _normalize_git_sha(getattr(_build_metadata, "GIT_COMMIT_SHA", None))


@functools.lru_cache(maxsize=1)
def get_profiler_version() -> str:
    """Return the profiler version string sent to the backend."""
    commit_sha = get_profiler_git_commit_sha()
    if not commit_sha or _FINAL_RELEASE_VERSION_RE.fullmatch(__version__):
        return __version__

    local_version_separator = "." if "+" in __version__ else "+"
    return f"{__version__}{local_version_separator}g{commit_sha}"
