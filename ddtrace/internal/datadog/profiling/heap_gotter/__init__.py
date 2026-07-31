"""Activator for native (C/C++) heap allocation profiling via GOT rewriting.

This module dlopen's libdatadog's ``libdd-profiling-heap-gotter-ffi`` cdylib
(staged as ``liblibdd_profiling_heap_gotter_ffi<EXT_SUFFIX>.so``; see
``src/native_heap_gotter/``) and drives it through the shared libdatadog C ABI:

    ddog_VoidResult ddog_heap_gotter_install(void);
    bool ddog_heap_gotter_is_installed(void);
    bool ddtrace_heap_gotter_live_heap_enabled(void);  # built with ddheap:free?

Calling ``install()`` patches the process's GOT entries for heap allocation
symbols so that Datadog's ``ddheap:alloc`` USDT probe sites fire on sampled
allocations. The OpenTelemetry eBPF profiler or Datadog Host Profiler then
attaches uprobes to those sites to collect native allocation flamegraphs. There
is nothing to collect or upload from the Python side — this only *arms* the
probes.

``live_heap_enabled()`` reports whether the loaded cdylib was *built* with
live-heap tracking, in which case it also emits the ``ddheap:free`` USDT and
stamps a per-allocation retain flag so the FH profiler can reconcile frees
against allocations for a live/retained-heap view. Live-heap is a default of the
gotter build, so any current cdylib reports ``True``; the query stays as a
defensive check that reflects the *actual* loaded artifact — an older alloc-only
cdylib (or a cdylib built before this symbol existed) reports ``False`` (the
symbol is bound defensively). This is a compile-time property, not a runtime
toggle.

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

# Whether the loaded cdylib was built with live-heap tracking (ddheap:free +
# retain flagging). Compile-time property of the artifact; see module docstring.
# Stays False when the cdylib is absent or was built allocation-only.
_live_heap_available: bool = False

# Whether the loaded cdylib exports the test-only hook-hit counter symbol
# (``ddog_heap_gotter_test_hook_hits``). Only true for a ``test-support``
# build (never a shipped wheel). Lets deterministic CI tests prove the patched
# GOT actually ran without a live eBPF attach; see ``test_hook_hits`` below.
_test_hook_available: bool = False

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

    # Bind the live-heap capability query defensively: it only exists on cdylibs
    # built at/after Phase 2. A missing symbol (older alloc-only build) simply
    # leaves live-heap reported as unavailable rather than failing the load.
    try:
        _lib.ddtrace_heap_gotter_live_heap_enabled.argtypes = []
        _lib.ddtrace_heap_gotter_live_heap_enabled.restype = ctypes.c_bool
        _live_heap_available = bool(_lib.ddtrace_heap_gotter_live_heap_enabled())
    except AttributeError:
        _live_heap_available = False

    # Bind the test-only hook-hit counter defensively: it exists ONLY on a
    # `test-support` build (never a shipped wheel). A missing symbol simply
    # leaves the counter reported as unavailable rather than failing the load.
    try:
        _lib.ddog_heap_gotter_test_hook_hits.argtypes = []
        _lib.ddog_heap_gotter_test_hook_hits.restype = ctypes.c_uint64
        _lib.ddtrace_heap_gotter_test_malloc_probe.argtypes = [ctypes.c_size_t]
        _lib.ddtrace_heap_gotter_test_malloc_probe.restype = ctypes.c_void_p
        _lib.ddtrace_heap_gotter_test_free_probe.argtypes = [ctypes.c_void_p]
        _lib.ddtrace_heap_gotter_test_free_probe.restype = None
        _test_hook_available = True
    except AttributeError:
        _test_hook_available = False

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

def live_heap_enabled() -> bool:
    """Return whether the loaded cdylib was built with live-heap tracking.

    Live-heap is a default of the gotter build, so any current cdylib returns
    True: it emits the ``ddheap:free`` USDT and stamps a per-allocation retain
    flag so the FH profiler can reconcile frees against allocations. A missing
    cdylib, or an older alloc-only one, returns False. Compile-time property; it
    does not change over the process lifetime.
    """
    return _live_heap_available


def test_hook_hits() -> int | None:
    """Test-only: number of times the patched GOT hooks have run in this process.

    Returns the process-global ``gotter_malloc``/``gotter_free`` hit counter,
    which increments on every intercepted raw (glibc) ``malloc``/``free`` — it is
    NOT sampling-gated — so a deterministic single-process test can prove the
    native gotter actually captured the raw-domain allocations that the
    in-process ``_memalloc`` sampler dropped under the ownership partition,
    without needing a live eBPF/Full-Host attach.

    Returns ``None`` when the counter is unavailable: on a non-Linux platform,
    when the cdylib is absent, or (the common CI case) when the shipped cdylib
    was NOT built with the ``test-support`` cargo feature. Tests must treat
    ``None`` as "skip: no test-support gotter build".
    """
    if not is_available or _lib is None or not _test_hook_available:
        return None
    try:
        return int(_lib.ddog_heap_gotter_test_hook_hits())
    except Exception:
        return None


def test_malloc_probe(size: int) -> int | None:
    """Test-only: allocate via malloc through the gotter cdylib's PLT/GOT.

    Unlike ``ctypes.CDLL(None).malloc``, which resolves libc with ``dlsym`` and
    bypasses patched GOT entries, this routes through the same relocation the
    interposer patches, so hook-hit counters advance when install succeeded.

    Returns the raw pointer as an integer, or ``None`` when unavailable.
    """
    if not is_available or _lib is None or not _test_hook_available:
        return None
    try:
        ptr = _lib.ddtrace_heap_gotter_test_malloc_probe(size)
        return int(ptr) if ptr else 0
    except Exception:
        return None


def test_free_probe(ptr: int) -> None:
    """Test-only: free a pointer from :func:`test_malloc_probe`."""
    if not is_available or _lib is None or not _test_hook_available or not ptr:
        return
    try:
        _lib.ddtrace_heap_gotter_test_free_probe(ptr)
    except Exception:
        return
