"""Activator for native (C/C++) heap allocation profiling via GOT rewriting.

This module dlopen's libdatadog's ``libdd-profiling-heap-gotter-ffi`` cdylib
(staged as ``liblibdd_profiling_heap_gotter_ffi<EXT_SUFFIX>.so``; see
``src/native_heap_gotter/``) and drives it through the shared libdatadog C ABI:

    ddog_VoidResult ddog_heap_gotter_install(void);
    bool ddog_heap_gotter_is_installed(void);

Calling ``install()`` patches the process's GOT entries for heap allocation
symbols so that Datadog's ``ddheap:alloc`` (Phase 1: allocation-only) USDT probe
sites fire on sampled allocations. The Full Host eBPF profiler then attaches
uprobes to those sites to collect native allocation flamegraphs. There is
nothing to collect or upload from the Python side — this only *arms* the probes.

Fail-closed by design: if the cdylib is missing (the default, since it only
ships when built with ``DD_PROFILING_NATIVE_HEAP_BUILD=1``) or anything goes
wrong loading it, ``is_available`` is ``False`` and ``install()`` is a no-op
returning ``False``. Loading this module must never raise.

Permanence: installation cannot be undone (the patched GOT entries point at
functions inside the cdylib), so the library must stay mapped for the life of
the process. We keep the ``ctypes.CDLL`` handle at module scope and never unload
it. After ``fork()`` the child inherits both the mapping and the patched GOT, so
a re-install in the child is a harmless idempotent no-op.
"""

from __future__ import annotations

import ctypes
import os
import sysconfig


# Upstream libdatadog FFI artifact base name (double-`lib` prefix).
_LIBRARY_BASENAME = "liblibdd_profiling_heap_gotter_ffi"

# Mirror cbindgen tags for ddog_VoidResult (common.h).
DDOG_VOID_RESULT_OK = 0
DDOG_VOID_RESULT_ERR = 1


class _DdogVecU8(ctypes.Structure):
    _fields_ = [
        ("ptr", ctypes.c_void_p),
        ("len", ctypes.c_size_t),
        ("capacity", ctypes.c_size_t),
    ]


class _DdogError(ctypes.Structure):
    _fields_ = [("message", _DdogVecU8)]


class _DdogVoidResult(ctypes.Structure):
    _fields_ = [
        ("tag", ctypes.c_uint32),
        ("err", _DdogError),
    ]


# Mirror the ddup/stack modules: importers (notably settings/profiling.py) read
# these two attributes to decide whether the feature can run.
is_available: bool = False
failure_msg: str = ""

_lib: ctypes.CDLL | None = None  # kept alive for process lifetime; never dlclose'd


def _library_path() -> str:
    suffix: str = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    profiling_dir: str = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(profiling_dir, _LIBRARY_BASENAME + suffix)


def _void_result_ok(result: _DdogVoidResult) -> bool:
    if result.tag == DDOG_VOID_RESULT_OK:
        return True
    if _lib is not None:
        try:
            _lib.ddog_Error_drop(ctypes.byref(result.err))
        except Exception:  # nosec: B110
            pass
    return False


try:
    # Native heap profiling via the gotter is Linux-only; on every other
    # platform the underlying library is a no-op, so don't even try to load.
    if os.name != "posix" or os.uname().sysname != "Linux":
        raise OSError("native heap gotter is only supported on Linux")

    _path: str = _library_path()
    if not os.path.exists(_path):
        raise FileNotFoundError(_path)

    # RTLD_GLOBAL so the loaded code is unambiguously resolvable; RTLD_NOW so any
    # unresolved symbol fails here (fail-closed) rather than at first call.
    _lib = ctypes.CDLL(_path, mode=ctypes.RTLD_GLOBAL | getattr(os, "RTLD_NOW", 0))

    _lib.ddog_heap_gotter_install.argtypes = []
    _lib.ddog_heap_gotter_install.restype = _DdogVoidResult
    _lib.ddog_heap_gotter_is_installed.argtypes = []
    _lib.ddog_heap_gotter_is_installed.restype = ctypes.c_bool
    _lib.ddog_Error_drop.argtypes = [ctypes.POINTER(_DdogError)]
    _lib.ddog_Error_drop.restype = None

    is_available = True

except Exception as e:
    failure_msg = str(e)
    _lib = None


def install() -> bool:
    """Install the native heap GOT overrides. Returns True if now installed.

    Idempotent and safe to call more than once (e.g. after fork). No-op that
    returns False when the cdylib is unavailable.
    """
    if not is_available or _lib is None:
        return False
    try:
        return _void_result_ok(_lib.ddog_heap_gotter_install())
    except Exception:
        return False


def is_installed() -> bool:
    """Return whether native heap GOT overrides are currently installed."""
    if not is_available or _lib is None:
        return False
    try:
        return bool(_lib.ddog_heap_gotter_is_installed())
    except Exception:
        return False
