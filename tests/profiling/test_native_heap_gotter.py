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
