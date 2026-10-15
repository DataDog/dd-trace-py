import os
import sysconfig
from typing import Optional


# Load the optional native frame walker only when caller information is requested.
_get_info_frame = None

# Cached paths for relativizing file paths (computed once at import time).
_CWD = os.path.abspath(os.getcwd())
_PURELIB_PATH = sysconfig.get_path("purelib") or ""
_STDLIB_PATH = sysconfig.get_path("stdlib") or ""


def rel_path(file_name: str) -> str:
    """Relativize an absolute file path for vulnerability/reachability reporting.

    Used by both IAST and SCA to produce short, readable paths in telemetry
    payloads.  Tries purelib first, then stdlib, then CWD-relative, then
    site-packages.  Returns empty string if the path cannot be relativized.
    """
    file_name_norm = file_name.replace("\\", "/")
    if file_name_norm.startswith(_PURELIB_PATH):
        return os.path.relpath(file_name_norm, start=_PURELIB_PATH)

    if file_name_norm.startswith(_STDLIB_PATH):
        return os.path.relpath(file_name_norm, start=_STDLIB_PATH)
    if file_name_norm.startswith(_CWD):
        return os.path.relpath(file_name_norm, start=_CWD)
    # If the path contains site-packages anywhere, return 'site-packages/<rest>'
    # Normalize separators to forward slashes for consistency
    if (idx := file_name_norm.find("/site-packages/")) != -1:
        return file_name_norm[idx:]
    return ""


def get_caller_frame_info() -> tuple[Optional[str], Optional[int], Optional[str], Optional[str]]:
    """Walk the stack and return (file_name, line_number, function_name, class_name).

    Uses the native C get_info_frame() to skip ddtrace, stdlib, and special
    frames, then relativizes the path.  Shared by IAST vulnerability
    reporting and SCA reachability detection.

    Returns (None, None, None, None) when no relevant frame is found.
    """
    global _get_info_frame
    if _get_info_frame is None:
        from ddtrace.internal._stacktrace import get_info_frame

        _get_info_frame = get_info_frame
    frame_info = _get_info_frame()
    if not frame_info or frame_info[0] in ("", -1, None):
        return None, None, None, None

    file_name, line_number, function_name, class_name = frame_info
    if not file_name:
        return None, None, None, None

    file_name = rel_path(file_name)
    if not file_name:
        return None, None, None, None

    return file_name, line_number, function_name, class_name
