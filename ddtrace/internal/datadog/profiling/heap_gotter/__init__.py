"""Activator for native (C/C++) heap allocation profiling via GOT rewriting.

Dlopens ``libdd_heap_gotter`` (see ``src/native_heap_gotter``) and drives:

    bool ddtrace_heap_gotter_install(void);
    bool ddtrace_heap_gotter_is_installed(void);
    bool ddtrace_heap_gotter_live_heap_enabled(void);  # True if built with ddheap:free

``install()`` patches GOT entries so ``ddheap:alloc`` (and, on live-heap builds,
``ddheap:free``) USDT sites fire; the Full Host eBPF profiler attaches uprobes.
Nothing is collected or uploaded from Python.

``live_heap_enabled()`` is a compile-time property of the loaded artifact
(default-on ``live-heap`` feature): True when the cdylib stamps retain flags and
emits ``ddheap:free``. False if the cdylib is missing, alloc-only
(``--no-default-features``), or predates this symbol (bound defensively).

Missing/broken load → ``is_available`` False, ``install()`` no-op; import never
raises. Install is permanent (GOT points into the cdylib), so the CDLL handle
stays mapped for the process lifetime.

After a successful ``install()``, fork children inherit the mapping and patched
GOT; ``_armed`` skips re-entering the native installer. Upstream has no
``pthread_atfork`` reset for its registry mutex — prefer arming on the main
thread or in the worker after fork; mid-install fork is unsafe.
"""

from __future__ import annotations

import ctypes
import os
import sysconfig


# Mirror ddup/stack: settings/profiling.py reads these to gate the feature.
is_available: bool = False
failure_msg: str = ""

# Compile-time live-heap capability of the loaded artifact (see module docstring).
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

    # RTLD_GLOBAL for resolvability; RTLD_NOW fail-closed on unresolved symbols.
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
    """Install native heap GOT overrides. True if installed; False otherwise.

    Idempotent at the Python layer: after success (including in a fork child that
    inherited ``_armed``), further calls return True without re-entering the native
    installer. See module docstring for fork-safety limits.
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
    """Return whether the loaded cdylib was built with live-heap tracking.

    Compile-time property of the artifact (default-on feature). False when the
    cdylib is missing, alloc-only, or predates this symbol.
    """
    return _live_heap_available
