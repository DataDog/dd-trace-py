"""Activator for native (C/C++) heap allocation profiling via GOT rewriting.

This module dlopen's the ``libdd_heap_gotter`` cdylib (built out-of-band from
libdatadog's ``libdd-profiling-heap-gotter-ffi``; see ``src/native_heap_gotter``)
and drives it through a tiny, stable C ABI:

    bool ddtrace_heap_gotter_install(void);           # install + report success
    bool ddtrace_heap_gotter_is_installed(void);       # current install state
    bool ddtrace_heap_gotter_live_heap_enabled(void);  # built with ddheap:free?

Calling ``install()`` patches the process's GOT entries for heap allocation
symbols so that Datadog's ``ddheap:alloc`` USDT probe sites fire on sampled
allocations. The Full Host eBPF profiler then attaches uprobes to those sites to
collect native allocation flamegraphs. There is nothing to collect or upload
from the Python side — this only *arms* the probes.

``live_heap_enabled()`` reports whether the loaded cdylib was *built* with
live-heap tracking (Phase 2), in which case it also emits the ``ddheap:free``
USDT and stamps a per-allocation retain flag so the FH profiler can reconcile
frees against allocations for a live/retained-heap view. This is a compile-time
property of the artifact (``DD_PROFILING_NATIVE_HEAP_LIVE=1`` at build), not a
runtime toggle; an allocation-only build reports ``False``. Older cdylibs built
before this symbol existed also report ``False`` (the symbol is bound
defensively).

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


# Mirror the ddup/stack modules: importers (notably settings/profiling.py) read
# these two attributes to decide whether the feature can run.
is_available: bool = False
failure_msg: str = ""

# Whether the loaded cdylib was built with live-heap tracking (ddheap:free +
# retain flagging). Compile-time property of the artifact; see module docstring.
# Stays False when the cdylib is absent or was built allocation-only.
_live_heap_available: bool = False

_lib: ctypes.CDLL | None = None  # kept alive for process lifetime; never dlclose'd


def _library_path() -> str:
    # The cdylib is staged next to libdd_wrapper in the profiling package and
    # carries the interpreter EXT_SUFFIX, matching setup.py's naming.
    suffix: str = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    profiling_dir: str = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(profiling_dir, "libdd_heap_gotter" + suffix)


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

    _lib.ddtrace_heap_gotter_install.argtypes = []
    _lib.ddtrace_heap_gotter_install.restype = ctypes.c_bool
    _lib.ddtrace_heap_gotter_is_installed.argtypes = []
    _lib.ddtrace_heap_gotter_is_installed.restype = ctypes.c_bool

    is_available = True

    # Bind the live-heap capability query defensively: it only exists on cdylibs
    # built at/after Phase 2. A missing symbol (older alloc-only build) simply
    # leaves live-heap reported as unavailable rather than failing the load.
    try:
        _lib.ddtrace_heap_gotter_live_heap_enabled.argtypes = []
        _lib.ddtrace_heap_gotter_live_heap_enabled.restype = ctypes.c_bool
        _live_heap_available = bool(_lib.ddtrace_heap_gotter_live_heap_enabled())
    except AttributeError:
        _live_heap_available = False

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
        return bool(_lib.ddtrace_heap_gotter_install())
    except Exception:
        return False


def is_installed() -> bool:
    """Return whether native heap GOT overrides are currently installed."""
    if not is_available or _lib is None:
        return False
    try:
        return bool(_lib.ddtrace_heap_gotter_is_installed())
    except Exception:
        return False


def live_heap_enabled() -> bool:
    """Return whether the loaded cdylib was built with live-heap tracking.

    True only for a Phase 2 (``DD_PROFILING_NATIVE_HEAP_LIVE=1``) build, which
    also emits the ``ddheap:free`` USDT and stamps a per-allocation retain flag
    so the FH profiler can reconcile frees against allocations. Allocation-only
    builds (and a missing cdylib) return False. Compile-time property; it does
    not change over the process lifetime.
    """
    return _live_heap_available
