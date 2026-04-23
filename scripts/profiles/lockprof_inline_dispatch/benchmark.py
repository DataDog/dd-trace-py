#!/usr/bin/env python3
# noqa: INP001 — run as script from repo root
"""Microbenchmark: inlined sampler check vs shared def helper in _ProfiledLock.

This answers a review question on PR #17639:

    > Are we sure duplicating the sampler.capture() check across
    > acquire/__enter__/__aenter__ is really worth it? Would these function
    > calls be Python ABI calls or simply C calls (in which case they
    > could/would probably be inlined by the compiler)?

_ProfiledLock is a regular Python class (``class``, not ``cdef class``).
A ``def`` method on a regular Python class goes through the full Python
call machinery — frame creation, argument tuple/dict packing, ref-counting
— even when the caller is Cython. The C compiler cannot inline that.

This benchmark compares three shapes, all wrapping a real ``threading.Lock()``
and using the production ``CaptureSampler`` configured at ``capture_pct=0.0``
so every call stays on the unsampled hot path:

* ``bench_inline``   — PR shape: ``acquire`` inlines the sampler check and
                        short-circuits to ``__wrapped__.acquire``; ``release``
                        inlines the ``acquired_time is None`` guard.
* ``bench_dispatch`` — "refactored to shared helper" shape: ``acquire`` calls
                        ``_acquire(inner_func, ...)``, which performs the
                        check. One extra ``def`` call per acquire/release on
                        the unsampled path.
* ``bench_cdef``     — ``cdef class`` with a ``cdef inline bint`` helper.
                        Here the C compiler *can* inline the dispatch, at
                        the cost of a much larger refactor (cdef class
                        constraints). Shown for context.

Run from the repository root::

    python scripts/profiles/lockprof_inline_dispatch/benchmark.py
"""

from __future__ import annotations

import importlib
import os
import shutil
import statistics
import sys
import tempfile
import threading
import timeit


try:
    from Cython.Build import cythonize
    from setuptools import Distribution
    from setuptools import Extension
except ImportError as exc:  # pragma: no cover
    print(
        "This benchmark needs Cython + setuptools: pip install cython setuptools",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

from ddtrace.profiling.collector._sampler import CaptureSampler


HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))


def _load_bench_modules():
    """Cythonize and build the paired bench *.pyx files, then import them.

    We build into a temp directory (rather than using pyximport) so we can
    pass ``include_path=[REPO_ROOT]`` to ``cythonize``, which is needed for
    the ``cimport CaptureSampler`` in each bench to resolve the in-tree
    ``ddtrace/profiling/collector/_sampler.pxd``.
    """
    build_dir = tempfile.mkdtemp(prefix="lockprof_inline_dispatch_")
    modules = ("bench_inline", "bench_dispatch", "bench_cdef")

    extensions = [
        Extension(
            name=name,
            sources=[os.path.join(HERE, f"{name}.pyx")],
            include_dirs=[REPO_ROOT],
            language="c",
        )
        for name in modules
    ]
    ext_modules = cythonize(
        extensions,
        include_path=[REPO_ROOT],
        build_dir=build_dir,
        language_level=3,
        quiet=True,
    )

    dist = Distribution({"ext_modules": ext_modules})
    cmd = dist.get_command_obj("build_ext")
    cmd.build_lib = build_dir
    cmd.build_temp = build_dir
    cmd.inplace = False
    cmd.ensure_finalized()
    cmd.run()

    if build_dir not in sys.path:
        sys.path.insert(0, build_dir)

    cy_inline = importlib.import_module("bench_inline")
    cy_dispatch = importlib.import_module("bench_dispatch")
    cy_cdef = importlib.import_module("bench_cdef")

    # Keep build_dir around until process exit — some import caches still reference it.
    _load_bench_modules._build_dir = build_dir

    return cy_inline, cy_dispatch, cy_cdef


def _cleanup_build_dir() -> None:
    build_dir = getattr(_load_bench_modules, "_build_dir", None)
    if build_dir and os.path.isdir(build_dir):
        shutil.rmtree(build_dir, ignore_errors=True)


def _bench(
    label: str,
    stmt: str,
    *,
    repeat: int = 9,
    number: int = 500_000,
    extra_globals: dict[str, object] | None = None,
) -> tuple[float, float]:
    bench_globals: dict[str, object] = dict(globals())
    if extra_globals:
        bench_globals.update(extra_globals)
    times = timeit.repeat(stmt, setup="pass", repeat=repeat, number=number, globals=bench_globals)
    per_op_ns_min = (min(times) / number) * 1e9
    per_op_ns_med = (statistics.median(times) / number) * 1e9
    print(
        f"  {label:<10s}: {per_op_ns_min:7.1f} ns/op  "
        f"(min of {repeat} × {number:,} ops; median {per_op_ns_med:.1f} ns/op)"
    )
    return per_op_ns_min, per_op_ns_med


def main() -> None:
    try:
        cy_inline, cy_dispatch, cy_cdef = _load_bench_modules()
    except Exception as exc:  # noqa: BLE001
        print(
            f"Failed to compile bench *.pyx (need Cython + setuptools + C toolchain).\n  {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    sampler = CaptureSampler(capture_pct=0.0)
    native = threading.Lock()

    lock_inline = cy_inline.Lock(native, sampler)
    lock_dispatch = cy_dispatch.Lock(native, sampler)
    lock_cdef = cy_cdef.Lock(native, sampler)
    native_only = threading.Lock()

    extra = {
        "lock_inline": lock_inline,
        "lock_dispatch": lock_dispatch,
        "lock_cdef": lock_cdef,
        "native_only": native_only,
    }

    print("=" * 74)
    print("  acquire() + release() — unsampled hot path (capture_pct=0.0)")
    print("=" * 74)

    print("\nacquire()+release() pair:")
    pair_native = _bench(
        "NATIVE",
        "native_only.acquire(); native_only.release()",
        extra_globals=extra,
    )
    pair_inline = _bench(
        "INLINE",
        "lock_inline.acquire(); lock_inline.release()",
        extra_globals=extra,
    )
    pair_dispatch = _bench(
        "DISPATCH",
        "lock_dispatch.acquire(); lock_dispatch.release()",
        extra_globals=extra,
    )
    pair_cdef = _bench(
        "CDEF",
        "lock_cdef.acquire(); lock_cdef.release()",
        extra_globals=extra,
    )

    print("\nacquire() only (isolates the extra _acquire dispatch):")
    acq_native = _bench(  # noqa: F841
        "NATIVE",
        "native_only.acquire(); native_only.release()",
        extra_globals=extra,
    )
    acq_inline = _bench(
        "INLINE",
        "lock_inline.acquire(); native.release()",
        extra_globals={**extra, "native": native},
    )
    acq_dispatch = _bench(
        "DISPATCH",
        "lock_dispatch.acquire(); native.release()",
        extra_globals={**extra, "native": native},
    )

    print("\nrelease() only (isolates the extra _release dispatch):")
    _ = _bench(
        "NATIVE",
        "native_only.acquire(); native_only.release()",
        extra_globals=extra,
    )
    rel_inline = _bench(
        "INLINE",
        "native.acquire(); lock_inline.release()",
        extra_globals={**extra, "native": native},
    )
    rel_dispatch = _bench(
        "DISPATCH",
        "native.acquire(); lock_dispatch.release()",
        extra_globals={**extra, "native": native},
    )

    # ------------------------------------------------------------------
    # Summary: absolute deltas attributable to the extra def-method call
    # ------------------------------------------------------------------
    print("\n" + "=" * 74)
    print("  Summary — cost of the extra def-helper dispatch on the hot path")
    print("=" * 74)
    delta_pair = pair_dispatch[0] - pair_inline[0]
    delta_acq = acq_dispatch[0] - acq_inline[0]
    delta_rel = rel_dispatch[0] - rel_inline[0]
    overhead_inline_vs_native = pair_inline[0] - pair_native[0]
    overhead_dispatch_vs_native = pair_dispatch[0] - pair_native[0]
    overhead_cdef_vs_native = pair_cdef[0] - pair_native[0]

    print(f"\n  INLINE  wrapper overhead vs native Lock: {overhead_inline_vs_native:+7.1f} ns/pair")
    print(f"  DISPATCH wrapper overhead vs native Lock: {overhead_dispatch_vs_native:+7.1f} ns/pair")
    print(f"  CDEF    wrapper overhead vs native Lock: {overhead_cdef_vs_native:+7.1f} ns/pair")

    print(f"\n  DISPATCH − INLINE (acquire+release pair): {delta_pair:+7.1f} ns/pair")
    print(f"  DISPATCH − INLINE (acquire only):         {delta_acq:+7.1f} ns/op")
    print(f"  DISPATCH − INLINE (release only):         {delta_rel:+7.1f} ns/op")

    print(
        "\n  Interpretation: the DISPATCH–INLINE gap is the cost of one extra\n"
        "  Python `def`-method call per acquire (and per release) on the 99%\n"
        "  unsampled hot path. That cost scales linearly with lock traffic —\n"
        "  e.g. 50 ns/pair × 1e7 acquire/release pairs/s = 500 ms/s of extra\n"
        "  CPU time in a busy app. The CDEF row shows what a full refactor to\n"
        "  a cdef class would buy (cdef inline helper, C-level dispatch), but\n"
        "  that comes with extension-type constraints that _ProfiledLock\n"
        "  currently cannot satisfy.\n"
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup_build_dir()
