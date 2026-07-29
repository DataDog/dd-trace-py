"""Smoke tests for the native (C/C++) heap profiling activator.

The activator (``ddtrace.internal.datadog.profiling.heap_gotter``) is fail-closed
and must behave correctly whether or not the opt-in gotter cdylib was built into
the wheel (``DD_PROFILING_NATIVE_HEAP_BUILD=1``):

* If the library is absent (the default), ``install()``/``is_installed()`` are
  no-ops returning ``False``.
* If present (a native-heap build on Linux), ``install()`` patches the process
  GOT and ``is_installed()`` flips to ``True`` and stays there (idempotent).

Proving that the ``ddheap`` USDT probes actually *fire* requires attaching the
Full Host eBPF profiler (or a ``test-support`` build exposing the hook-hit
counter) and is validated in the staging dogfood, not here.
"""

import sys
from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    # We need the pyright: ignore because pprof_pb2 does not exist as a real module, only as a pyi.
    from tests.profiling.collector import pprof_pb2  # pyright: ignore[reportMissingModuleSource]


# Evaluated in the PARENT interpreter (subprocess bodies cannot express a skip:
# an in-body ``pytest.skip`` would surface as a non-zero exit and FAIL the outer
# test). ``test_hook_hits()`` is a read-only counter query with no side effects —
# it does NOT install the gotter — so it is safe to call at import time. It
# returns ``None`` unless the loaded cdylib was built with the ``test-support``
# cargo feature (Linux 64-bit, ``DD_PROFILING_NATIVE_HEAP_TEST_SUPPORT=1``),
# which the standard CI wheel is not, so the end-to-end handoff proof below skips
# everywhere except a dedicated test-support build.
try:
    from ddtrace.internal.datadog.profiling import heap_gotter as _heap_gotter

    _GOTTER_TEST_HOOK_AVAILABLE: bool = _heap_gotter.test_hook_hits() is not None
except Exception:
    _GOTTER_TEST_HOOK_AVAILABLE = False


@pytest.mark.skipif(sys.platform != "linux", reason="native heap gotter is Linux-only")
@pytest.mark.subprocess
def test_native_heap_gotter_smoke() -> None:
    # Runs in a fresh subprocess: install() patches the process GOT permanently,
    # so we must not do it in the shared test interpreter.
    from ddtrace.internal.datadog.profiling import heap_gotter

    if not heap_gotter.is_available:
        # Wheel built without the gotter cdylib: strictly a no-op.
        assert heap_gotter.install() is False
        assert heap_gotter.is_installed() is False
        # live_heap_enabled must be a safe False no-op when the cdylib is absent.
        assert heap_gotter.live_heap_enabled() is False
    else:
        # Native-heap build: arming must take effect and be idempotent.
        assert heap_gotter.is_installed() is False
        assert heap_gotter.install() is True
        assert heap_gotter.is_installed() is True
        assert heap_gotter.install() is True

        # live-heap is a compile-time property; the query must return a bool and
        # never crash regardless of whether this build enabled the feature.
        assert isinstance(heap_gotter.live_heap_enabled(), bool)

        # Generate allocation pressure; this must not crash with the patched GOT.
        blobs: list[tuple[str, int]] = []
        for i in range(200):
            blobs.append(("x" * 4096, i))
        assert len(blobs) == 200


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLED="true"))
def test_profiler_start_arms_native_heap_when_enabled() -> None:
    """Starting the profiler with native heap enabled invokes the activator.

    Cross-platform: we force the config flag on (the import-time availability
    gate would otherwise disable it when the cdylib is absent) and patch the
    activator, so this exercises only the profiler wiring, not the real library.
    """
    from unittest import mock

    from ddtrace.internal.datadog.profiling import heap_gotter
    from ddtrace.internal.settings.profiling import config as profiling_config

    # Force on regardless of whether the cdylib shipped in this build.
    profiling_config.native_heap.enabled = True  # pyright: ignore[reportAttributeAccessIssue]

    with mock.patch.object(heap_gotter, "install", return_value=True) as install:
        from ddtrace.profiling.profiler import Profiler

        prof: Profiler = Profiler()
        prof.start()
        try:
            assert install.called, "profiler start should arm native heap profiling when enabled"
        finally:
            prof.stop(flush=False)


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLED="true"))
def test_profiler_start_skips_native_heap_when_disabled() -> None:
    """With native heap disabled, the profiler must not touch the activator.

    This guards the zero-overhead promise of the disabled path: no install()
    call (and therefore no dlopen of the gotter cdylib) when the feature is off.
    """
    from unittest import mock

    from ddtrace.internal.datadog.profiling import heap_gotter
    from ddtrace.internal.settings.profiling import config as profiling_config

    profiling_config.native_heap.enabled = False  # pyright: ignore[reportAttributeAccessIssue]

    with mock.patch.object(heap_gotter, "install", return_value=True) as install:
        from ddtrace.profiling.profiler import Profiler

        prof: Profiler = Profiler()
        prof.start()
        try:
            assert not install.called, "profiler must not arm native heap profiling when disabled"
        finally:
            prof.stop(flush=False)


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLED="true"))
def test_profiler_start_survives_native_heap_install_error() -> None:
    """A failure while arming native heap profiling must not break the profiler.

    Arming is best-effort: if install() raises, profiler startup swallows it and
    the profiler still comes up.
    """
    from unittest import mock

    from ddtrace.internal.datadog.profiling import heap_gotter
    from ddtrace.internal.settings.profiling import config as profiling_config

    profiling_config.native_heap.enabled = True  # pyright: ignore[reportAttributeAccessIssue]

    with mock.patch.object(heap_gotter, "install", side_effect=RuntimeError("boom")) as install:
        from ddtrace.profiling.profiler import Profiler

        prof: Profiler = Profiler()
        prof.start()  # must not raise
        try:
            assert install.called
            assert prof.status.value == "running"
        finally:
            prof.stop(flush=False)


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLED="true"))
def test_profiler_keeps_managed_heap_when_native_heap_armed() -> None:
    """Ownership partition (Phase 2 de-dup): the partition is by allocator domain.

    The gotter owns native / raw glibc ``malloc`` (RAW domain, direct C-ext/numpy
    allocations, pymalloc arena refills). The in-process ``_memalloc`` collector
    hooks ONLY the pymalloc-managed OBJ/MEM domains and never the RAW domain, so
    the two producers are domain-disjoint. Arming the gotter must therefore KEEP
    the in-process managed-heap sampler running — dropping it would lose the
    Python-managed (pymalloc OBJ/MEM) heap profile the gotter never produces.
    """
    from unittest import mock

    from ddtrace.internal.datadog.profiling import heap_gotter
    from ddtrace.internal.settings.profiling import config as profiling_config
    from ddtrace.profiling.collector import memalloc

    # Force on regardless of whether the cdylib shipped, and simulate a
    # successful arm (install() returning True).
    profiling_config.native_heap.enabled = True  # pyright: ignore[reportAttributeAccessIssue]

    with mock.patch.object(heap_gotter, "install", return_value=True) as install:
        with mock.patch.object(heap_gotter, "live_heap_enabled", return_value=False):
            with mock.patch.object(memalloc, "set_native_heap_partition") as set_partition:
                from ddtrace.profiling.profiler import Profiler

                prof: Profiler = Profiler()
                prof.start()
                try:
                    assert install.called, "the gotter must still be armed when native heap is enabled"
                    has_mem: bool = any(isinstance(c, memalloc.MemoryCollector) for c in prof._profiler._collectors)
                    assert has_mem, (
                        "in-process managed-heap (OBJ/MEM) collector must stay active when the gotter is armed; "
                        "the gotter owns only the native/raw glibc malloc domain"
                    )
                    # The producer-side size partition must be turned on so the
                    # in-process sampler drops the > 512B tail the gotter owns.
                    set_partition.assert_called_once_with(True)
                finally:
                    prof.stop(flush=False)


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLED="true"))
def test_profiler_keeps_managed_heap_when_gotter_not_installed() -> None:
    """Fail-safe: if native heap is enabled but the gotter did NOT install
    (install() returned False), the in-process sampler must remain active so
    heap profiling is never silently lost.
    """
    from unittest import mock

    from ddtrace.internal.datadog.profiling import heap_gotter
    from ddtrace.internal.settings.profiling import config as profiling_config
    from ddtrace.profiling.collector import memalloc

    profiling_config.native_heap.enabled = True  # pyright: ignore[reportAttributeAccessIssue]

    with mock.patch.object(heap_gotter, "install", return_value=False):
        with mock.patch.object(memalloc, "set_native_heap_partition") as set_partition:
            from ddtrace.profiling.profiler import Profiler

            prof: Profiler = Profiler()
            prof.start()
            try:
                has_mem: bool = any(isinstance(c, memalloc.MemoryCollector) for c in prof._profiler._collectors)
                assert has_mem, "in-process memory collector must stay active when the gotter fails to install"
                # Not armed -> partition off so ALL sizes keep being sampled.
                set_partition.assert_called_once_with(False)
            finally:
                prof.stop(flush=False)


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLED="true"))
def test_profiler_keeps_managed_heap_when_native_heap_disabled() -> None:
    """With native heap disabled, the in-process memory collector runs unchanged
    and the gotter is never armed.
    """
    from unittest import mock

    from ddtrace.internal.datadog.profiling import heap_gotter
    from ddtrace.internal.settings.profiling import config as profiling_config
    from ddtrace.profiling.collector import memalloc

    profiling_config.native_heap.enabled = False  # pyright: ignore[reportAttributeAccessIssue]

    # install() is patched to True to prove arming keys on the feature being
    # enabled, not merely on install() — it must never be called here.
    with mock.patch.object(heap_gotter, "install", return_value=True) as install:
        with mock.patch.object(memalloc, "set_native_heap_partition") as set_partition:
            from ddtrace.profiling.profiler import Profiler

            prof: Profiler = Profiler()
            prof.start()
            try:
                assert not install.called
                has_mem: bool = any(isinstance(c, memalloc.MemoryCollector) for c in prof._profiler._collectors)
                assert has_mem, "in-process memory collector must run when native heap is disabled"
                # Feature off -> partition off (all sizes sampled).
                set_partition.assert_called_once_with(False)
            finally:
                prof.stop(flush=False)


# ---------------------------------------------------------------------------
# End-to-end producer-side ownership handoff (Phase 2 de-dup)
#
# The tests above prove the *wiring* (arming turns the partition on) and
# tests/profiling/collector/test_memalloc.py proves the *in-process* half of the
# partition (> 512B managed allocations are dropped, <= 512B kept, and with the
# partition off everything is sampled). What neither can prove without a live
# eBPF/Full-Host attach is the *other* half of the handoff: that the native
# gotter actually captures the > 512B raw glibc-malloc tail the in-process
# sampler drops — i.e. that exactly one producer owns each allocation.
#
# The test below closes that gap deterministically in a single process using the
# gotter's built-in ``test-support`` hook-hit counter, replacing the flaky
# staging A/B dedup signal with an in-CI assertion. It requires a Linux 64-bit
# ``test-support`` gotter build (see the module-level skip note); it skips in the
# standard CI wheel, which ships no gotter at all.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "linux" or not _GOTTER_TEST_HOOK_AVAILABLE,
    reason=(
        "needs a Linux 64-bit test-support gotter build exposing "
        "ddtrace_heap_gotter_test_hook_hits() (build with "
        "DD_PROFILING_NATIVE_HEAP_BUILD=1 DD_PROFILING_NATIVE_HEAP_TEST_SUPPORT=1); "
        "the standard CI wheel ships no gotter"
    ),
)
@pytest.mark.subprocess
def test_native_heap_ownership_handoff_end_to_end() -> None:
    """Deterministic, cluster-independent proof of the Phase 2 ownership handoff.

    With the gotter armed and the producer-side size partition on, a > 512B
    managed OBJ allocation must be owned by *exactly one* producer:

      (a) it is NOT sampled by the in-process ``_memalloc`` heap profiler (the
          partition drops the > 512B tail), AND
      (b) it IS seen by the native gotter — the process-global hook-hit counter
          advances by at least one per large allocation, proving the patched GOT
          captured the raw glibc ``malloc`` the in-process sampler dropped.

    A <= 512B control allocation stays pymalloc-pool-served and is still sampled
    in-process, confirming the partition splits by size rather than dropping
    everything. Runs in a subprocess because ``install()`` patches the process
    GOT permanently and the partition flag is process-global.
    """
    import os
    import tempfile

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import heap_gotter
    from ddtrace.profiling.collector import memalloc
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_memalloc import _PARTITION_LARGE_ALLOC_COUNT
    from tests.profiling.collector.test_memalloc import _allocate_large_buffers
    from tests.profiling.collector.test_memalloc import _allocate_small_objects
    from tests.profiling.collector.test_memalloc import _count_heap_samples_with_function

    # Defensive: the module-level skipif already gated on these, but assert so a
    # mis-configured skip can never let this test pass vacuously.
    assert heap_gotter.is_available, "test requires the gotter cdylib to be present"
    assert heap_gotter.test_hook_hits() is not None, "test requires a test-support gotter build"

    # Arm the native producer (permanent + process-global; hence @subprocess).
    assert heap_gotter.install() is True
    assert heap_gotter.is_installed() is True

    prefix: str = os.path.join(tempfile.mkdtemp(), "handoff")
    output_filename: str = prefix + "." + str(os.getpid())
    ddup.config(
        service="test_native_heap_ownership_handoff",
        version="test",
        env="test",
        output_filename=prefix,
    )
    ddup.start()

    store: list[object] = []
    mc: memalloc.MemoryCollector = memalloc.MemoryCollector(heap_sample_size=64 * 1024)
    memalloc.set_native_heap_partition(True)
    try:
        with mc:
            # Measure the native counter strictly around the > 512B allocations.
            # The counter is process-global and increments on EVERY intercepted
            # raw malloc (it is NOT sampling-gated), so background allocations
            # can only inflate the delta — never shrink it below the number of
            # large buffers we deliberately allocate.
            hits_before: "int | None" = heap_gotter.test_hook_hits()
            _allocate_large_buffers(store)
            hits_after: "int | None" = heap_gotter.test_hook_hits()

            _allocate_small_objects(store)
            mc.snapshot()
        ddup.upload()

        profile: "pprof_pb2.Profile" = pprof_utils.parse_newest_profile(output_filename)
        heap_samples: "list[pprof_pb2.Sample]" = pprof_utils.get_samples_with_value_type(profile, "heap-space")

        # (a) In-process producer dropped the > 512B tail ...
        large_count: int = _count_heap_samples_with_function(profile, heap_samples, "_allocate_large_buffers")
        assert large_count == 0, (
            f"partition ON: > 512B managed allocations must NOT be sampled in-process (got {large_count})"
        )

        # (b) ... and the native producer captured it. Each > 512B bytes object
        # is a single raw malloc routed through the patched GOT, so the hook-hit
        # counter must advance by at least the number of large buffers.
        assert hits_before is not None and hits_after is not None
        delta: int = hits_after - hits_before
        assert delta >= _PARTITION_LARGE_ALLOC_COUNT, (
            "native gotter must capture the > 512B raw-malloc tail the in-process sampler dropped "
            f"(hook-hit delta {delta} < {_PARTITION_LARGE_ALLOC_COUNT} large allocations)"
        )

        # Control: <= 512B pool-served allocations are invisible to the gotter
        # and must still be sampled in-process — the partition splits by size.
        small_count: int = _count_heap_samples_with_function(profile, heap_samples, "_allocate_small_objects")
        assert small_count > 0, "partition ON: <= 512B managed allocations must still be sampled in-process"
    finally:
        # Reset the process-global flag so it cannot bleed into other tests
        # sharing this interpreter (belt-and-braces; the subprocess exits anyway).
        memalloc.set_native_heap_partition(False)

    del store
