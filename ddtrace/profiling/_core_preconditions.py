"""Linux core-dump preconditions reported once at profiler startup."""

import ctypes
import resource
import sys

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL


# linux/prctl.h. /proc/self/status has CoreDumping (dump in progress), not Dumpable.
_PR_GET_DUMPABLE: int = 3


def _categorize_rlimit(limit: int) -> str:
    if limit == 0:
        return "zero"
    if limit == resource.RLIM_INFINITY:
        return "unlimited"
    return "limited"


def _read_core_pattern_shape() -> str:
    try:
        with open("/proc/sys/kernel/core_pattern", encoding="utf-8") as core_pattern_file:
            pattern: str = core_pattern_file.read().strip()
    except OSError:
        return "unknown"
    if not pattern or pattern.startswith("/dev/null"):
        return "disabled"
    if pattern.startswith("|"):
        if "systemd-coredump" in pattern:
            return "pipe_systemd"
        return "pipe_custom"
    if pattern.startswith("/"):
        return "file"
    return "other"


def _prctl_get_dumpable() -> int:
    libc: ctypes.CDLL = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    result: int = libc.prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0)
    if result < 0:
        err: int = ctypes.get_errno()
        raise OSError(err, "prctl(PR_GET_DUMPABLE) failed")
    return result


def _read_dumpable() -> str:
    try:
        value: int = _prctl_get_dumpable()
    except (OSError, AttributeError):
        return "unknown"
    if value == 1:
        return "yes"
    if value == 0:
        return "no"
    if value == 2:
        return "suid"
    return "unknown"


def _emit_linux_core_preconditions_telemetry() -> None:
    soft_limit: int
    hard_limit: int
    soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_CORE)
    telemetry_writer.add_log(
        TELEMETRY_LOG_LEVEL.DEBUG,
        "Profiler startup core dump preconditions",
        tags={
            "rlimit_core_soft": _categorize_rlimit(soft_limit),
            "rlimit_core_hard": _categorize_rlimit(hard_limit),
            "core_pattern": _read_core_pattern_shape(),
            "dumpable": _read_dumpable(),
        },
    )


def emit_core_preconditions_telemetry() -> None:
    """Report low-cardinality core-dump preconditions for fleet aggregation."""
    if sys.platform == "linux":
        _emit_linux_core_preconditions_telemetry()
