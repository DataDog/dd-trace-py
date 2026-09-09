"""Load libdd_heap_gotter and patch the process GOT so ddheap USDT probes fire.

C ABI (see `src/native_heap_gotter`)::

    bool ddtrace_heap_gotter_install(void);
    bool ddtrace_heap_gotter_is_installed(void);
    bool ddtrace_heap_gotter_live_heap_enabled(void);  # True if built with ddheap:free

Python never collects or uploads native-heap samples. The Full Host eBPF
profiler attaches to `ddheap:alloc` (and `ddheap:free` on live-heap
builds) after `install()` rewrites the GOT.

`DD_PROFILING_NATIVE_HEAP_ENABLED` is checked twice: at wheel build
(`setup.py` compiles the `.so`) and at process start (the profiler
imports this module and calls `install()`). Turning it on in a running
process cannot add a library that was not in the wheel.

libdatadog also reads `DD_HEAP_SAMPLING_ENABLED` (unset means on;
`0`/`false`/`no`/`off` skip the GOT patch). If it is off,
`install()` returns False.

`live_heap_enabled()` is about how the `.so` was compiled (live-heap
is on by default). It is False when the library is missing, alloc-only,
or old enough not to export the symbol.

If dlopen fails, `is_available` is False, `install()` is a no-op, and
import does not raise. A successful install is permanent — GOT entries
point into this library — so we keep the CDLL handle for the life of the
process.
"""

from __future__ import annotations

import ctypes
import os
import sysconfig


# Mirror ddup/stack: settings/profiling.py reads these to gate the feature.
is_available: bool = False
failure_msg: str = ""

# True if this .so was built with live-heap (ddheap:free). See module docstring.
_live_heap_available: bool = False

_lib: ctypes.CDLL | None = None  # process lifetime; never dlclose'd
_armed: bool = False  # successful install(); inherited across fork


def _library_path() -> str:
    # Staged next to libdd_wrapper with the interpreter EXT_SUFFIX (setup.py).
    suffix: str = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    profiling_dir: str = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(profiling_dir, "libdd_heap_gotter" + suffix)


try:
    # Linux-only; elsewhere the gotter is a no-op.
    sysname = os.uname().sysname if os.name == "posix" else os.name
    if sysname != "Linux":
        raise OSError(f"Native heap gotter is only supported on Linux. Running on {sysname}")

    _path: str = _library_path()
    if not os.path.exists(_path):
        raise FileNotFoundError(_path)

    # RTLD_GLOBAL: loaded code is unambiguously resolvable.
    # RTLD_NOW: any unresolved symbol fails here and not at first call.
    _lib = ctypes.CDLL(_path, mode=ctypes.RTLD_GLOBAL | getattr(os, "RTLD_NOW", 0))

    _lib.ddtrace_heap_gotter_install.argtypes = []
    _lib.ddtrace_heap_gotter_install.restype = ctypes.c_bool
    _lib.ddtrace_heap_gotter_is_installed.argtypes = []
    _lib.ddtrace_heap_gotter_is_installed.restype = ctypes.c_bool

    is_available = True

    # Optional symbol (pre-Phase-2 cdylibs); failure must not disable install().
    try:
        _lib.ddtrace_heap_gotter_live_heap_enabled.argtypes = []
        _lib.ddtrace_heap_gotter_live_heap_enabled.restype = ctypes.c_bool
        _live_heap_available = bool(_lib.ddtrace_heap_gotter_live_heap_enabled())
    except Exception:
        _live_heap_available = False

except Exception as e:
    failure_msg = str(e)
    _lib = None


def install() -> bool:
    """Patch heap GOT entries. True if they are installed afterwards.

    Safe to call more than once. After a successful install, later calls
    return True without talking to the native installer again. Fork children
    inherit the mapping and `_armed`, so they skip a second native install.

    Call this on the main thread, or in the worker after fork. Do not fork
    while it is running — upstream does not reset its registry mutex in
    `pthread_atfork`.
    """
    global _armed
    if not is_available or _lib is None:
        return False
    if _armed:
        return True
    try:
        ok = bool(_lib.ddtrace_heap_gotter_install())
    except Exception:
        return False
    if ok:
        _armed = True
    return ok


def is_installed() -> bool:
    """Return whether native heap GOT overrides are currently installed."""
    if not is_available or _lib is None:
        return False
    if _armed:
        return True
    try:
        return bool(_lib.ddtrace_heap_gotter_is_installed())
    except Exception:
        return False


def live_heap_enabled() -> bool:
    """Return whether the loaded cdylib was built with live-heap tracking."""
    return _live_heap_available
