"""Smoke tests for the native (C/C++) heap profiling activator.

``install()`` patches the process GOT and ``is_installed()`` flips to ``True``
and stays there (idempotent).

Proving that the ``ddheap`` USDT probes actually *fire* requires attaching the
Full Host eBPF profiler (or a ``test-support`` build exposing the hook-hit
counter) and is validated in the staging dogfood, not here.
"""

import sys

import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="native heap gotter is Linux-only")
@pytest.mark.subprocess(err=None)
def test_native_heap_gotter_smoke() -> None:
    # Runs in a fresh subprocess: install() patches the process GOT permanently,
    # so we must not do it in the shared test interpreter.
    from ddtrace.internal.datadog.profiling import heap_gotter

    if not heap_gotter.is_available:
        assert heap_gotter.install() is False
        assert heap_gotter.is_installed() is False
        assert heap_gotter.live_heap_enabled() is False
    else:
        assert heap_gotter.is_installed() is False
        assert heap_gotter.install() is True
        assert heap_gotter.is_installed() is True
        assert heap_gotter.install() is True  # idempotent
        assert isinstance(heap_gotter.live_heap_enabled(), bool)

        blobs: list[tuple[str, int]] = []
        for i in range(200):
            blobs.append(("x" * 4096, i))
        assert len(blobs) == 200


@pytest.mark.skipif(sys.platform != "linux", reason="native heap gotter is Linux-only")
@pytest.mark.subprocess(err=None)
def test_native_heap_gotter_fork_install_and_allocations() -> None:
    """dlopen + install, then fork and keep allocating in parent and child.

    Exercises the gunicorn/uWSGI-shaped path where the activator may run before
    fork and again in the child. GOT overrides are inherited across fork;
    fork + alloc must not crash.
    """
    import os

    from ddtrace.internal.datadog.profiling import heap_gotter

    # Import already dlopen'd. Arm in the parent.
    armed = heap_gotter.install()
    if heap_gotter.is_available:
        assert armed is True
        assert heap_gotter.is_installed() is True
    else:
        assert armed is False
        assert heap_gotter.is_installed() is False

    parent_blobs: list[tuple[str, int]] = [("x" * 4096, i) for i in range(50)]

    pid = os.fork()
    if pid == 0:
        try:
            # Child inherits mapping/GOT when armed; `_armed` skips re-entering the cdylib.
            assert isinstance(heap_gotter.install(), bool)
            if heap_gotter.is_available:
                assert heap_gotter.is_installed() is True
            child_blobs = [("y" * 4096, i) for i in range(100)]
            assert len(child_blobs) == 100
            os._exit(0)
        except Exception:
            os._exit(1)
    else:
        _, status = os.waitpid(pid, 0)
        assert not os.WIFSIGNALED(status), f"Child crashed with signal {os.WTERMSIG(status)}"
        assert os.WEXITSTATUS(status) == 0
        parent_blobs.append(("z" * 4096, 99))
        assert len(parent_blobs) == 51
        # Parent stays `_armed`; further install() calls skip the native path.
        assert isinstance(heap_gotter.install(), bool)


# err=None: Profiler.start() talks to the agent and collectors may log to stderr.
@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLED="true"), err=None)
def test_profiler_start_native_heap_install_idempotent_on_restart() -> None:
    """A second profiler start (e.g. uWSGI worker) calls install() again; `_armed` skips native re-entry."""
    from unittest import mock

    from ddtrace.internal.datadog.profiling import heap_gotter
    from ddtrace.internal.settings.profiling import config as profiling_config

    profiling_config.native_heap.enabled = True  # pyright: ignore[reportAttributeAccessIssue]

    with mock.patch.object(heap_gotter, "install", return_value=True) as install:
        from ddtrace.profiling.profiler import Profiler

        prof: Profiler = Profiler()
        prof.start()
        try:
            assert install.call_count == 1
            # Stop + start again (same path as a fresh worker start after fork).
            # Do not call _start_service() on a running instance — collectors are
            # already RUNNING and would raise ServiceStatusError.
            prof.stop(flush=False)
            prof.start()
            assert install.call_count == 2
        finally:
            prof.stop(flush=False)


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLED="true"), err=None)
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


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLED="true"), err=None)
def test_profiler_start_skips_native_heap_when_disabled() -> None:
    """With native heap disabled, the profiler must not import the activator.

    This guards the zero-overhead promise of the disabled path: no import of
    heap_gotter (and therefore no dlopen of the gotter cdylib) when the feature
    is off. Assert via ``sys.modules`` so the test itself does not trigger the
    import-time load.
    """
    import sys

    from ddtrace.internal.settings.profiling import config as profiling_config

    profiling_config.native_heap.enabled = False  # pyright: ignore[reportAttributeAccessIssue]

    module_name = "ddtrace.internal.datadog.profiling.heap_gotter"
    assert module_name not in sys.modules

    from ddtrace.profiling.profiler import Profiler

    prof: Profiler = Profiler()
    prof.start()
    try:
        assert module_name not in sys.modules, "profiler must not import heap_gotter when native heap is disabled"
    finally:
        prof.stop(flush=False)


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_ENABLED="true"),
    # install() failures are logged with exc_info=True. Ignore other stderr
    # (agent connection, collector start) the same way as the other profiler tests.
    err=lambda s: "Failed to arm native heap profiling" in s and "RuntimeError: boom" in s,
)
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
