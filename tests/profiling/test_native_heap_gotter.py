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

import pytest


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
    else:
        # Native-heap build: arming must take effect and be idempotent.
        assert heap_gotter.is_installed() is False
        assert heap_gotter.install() is True
        assert heap_gotter.is_installed() is True
        assert heap_gotter.install() is True

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

        prof = Profiler()
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

        prof = Profiler()
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

        prof = Profiler()
        prof.start()  # must not raise
        try:
            assert install.called
            assert prof.status.value == "running"
        finally:
            prof.stop(flush=False)
