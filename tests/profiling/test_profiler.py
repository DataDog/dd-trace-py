import logging
import os
import sys
import time
from unittest import mock

import pytest

import ddtrace
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.profiling import collector
from ddtrace.profiling import profiler
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import asyncio
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT") or False


@pytest.fixture(autouse=True)
def _reset_profiler_active_instance():
    yield
    profiler.Profiler._active_instance = None


def test_status():
    p = profiler.Profiler()
    assert repr(p.status) == "<ServiceStatus.STOPPED: 'stopped'>"
    p.start()
    assert repr(p.status) == "<ServiceStatus.RUNNING: 'running'>"
    p.stop(flush=False)
    assert repr(p.status) == "<ServiceStatus.STOPPED: 'stopped'>"


def test_restart():
    p = profiler.Profiler()
    p.start()
    p.stop(flush=False)
    p.start()
    p.stop(flush=False)


def test_multiple_stop():
    """Check that the profiler can be stopped twice."""
    p = profiler.Profiler()
    p.start()
    p.stop(flush=False)
    p.stop(flush=False)


def test_tracer_api(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "foobar")
    prof = profiler.Profiler(tracer=ddtrace.tracer)
    assert prof.tracer == ddtrace.tracer
    for col in prof._profiler._collectors:
        if isinstance(col, stack.StackCollector):
            assert col.tracer == ddtrace.tracer
            break
    else:
        pytest.fail("Unable to find stack collector")


@pytest.mark.subprocess()
def test_default_memory():
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import memalloc

    assert any(isinstance(col, memalloc.MemoryCollector) for col in profiler.Profiler()._profiler._collectors)


@pytest.mark.subprocess(env=dict(DD_PROFILING_MEMORY_ENABLED="true"))
def test_enable_memory():
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import memalloc

    assert any(isinstance(col, memalloc.MemoryCollector) for col in profiler.Profiler()._profiler._collectors)


@pytest.mark.subprocess(env=dict(DD_PROFILING_MEMORY_ENABLED="false"))
def test_disable_memory():
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import memalloc

    assert all(not isinstance(col, memalloc.MemoryCollector) for col in profiler.Profiler()._profiler._collectors)


def test_copy():
    p = profiler._ProfilerInstance(env="123", version="dwq", service="foobar")
    c = p.copy()
    assert c == p
    assert p.env == c.env
    assert p.version == c.version
    assert p.service == c.service
    assert p.tracer == c.tracer
    assert p.tags == c.tags


def test_failed_start_collector(caplog, monkeypatch):
    class ErrCollect(collector.Collector):
        def _start_service(self):
            raise RuntimeError("could not import required module")

        def _stop_service(self):
            pass

        @staticmethod
        def collect():
            pass

        @staticmethod
        def snapshot():
            raise Exception("error!")

    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")

    class TestProfiler(profiler._ProfilerInstance):
        def _build_default_exporters(self, *args, **kargs):
            return None

    p = TestProfiler()
    err_collector = mock.MagicMock(wraps=ErrCollect())
    p._collectors = [err_collector]
    p.start()

    def profiling_tuples(tuples):
        return [t for t in tuples if t[0].startswith("ddtrace.profiling")]

    assert profiling_tuples(caplog.record_tuples) == [
        ("ddtrace.profiling.profiler", logging.ERROR, "Failed to start collector %r, disabling." % err_collector)
    ]
    time.sleep(2)
    p.stop()
    assert err_collector.snapshot.call_count == 0
    assert profiling_tuples(caplog.record_tuples) == [
        ("ddtrace.profiling.profiler", logging.ERROR, "Failed to start collector %r, disabling." % err_collector)
    ]


def test_default_collectors():
    p = profiler.Profiler()
    assert any(isinstance(c, stack.StackCollector) for c in p._profiler._collectors)
    assert any(isinstance(c, threading.ThreadingLockCollector) for c in p._profiler._collectors)
    try:
        import asyncio as _  # noqa: F401
    except ImportError:
        pass
    else:
        assert any(isinstance(c, asyncio.AsyncioLockCollector) for c in p._profiler._collectors)
        assert any(isinstance(c, asyncio.AsyncioSemaphoreCollector) for c in p._profiler._collectors)
        assert any(isinstance(c, asyncio.AsyncioBoundedSemaphoreCollector) for c in p._profiler._collectors)
        assert any(isinstance(c, asyncio.AsyncioConditionCollector) for c in p._profiler._collectors)
    p.stop(flush=False)


def test_stop_unregisters_pytorch_hook_when_lock_collector_disabled(monkeypatch):
    registered_hooks = []
    unregistered_hooks = []

    class WatchdogMock(object):
        @staticmethod
        def register_module_hook(module, hook):
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module, hook):
            unregistered_hooks.append((module, hook))

    class TestProfiler(profiler._ProfilerInstance):
        def _build_default_exporters(self, *args, **kargs):
            return None

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)

    p = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=True,
    )
    p._scheduler = mock.Mock()

    p.start()
    p.stop(flush=False)

    assert [module for module, _ in registered_hooks] == ["torch"]
    assert unregistered_hooks == registered_hooks


def test_stop_unregisters_all_import_hooks_for_lock_and_pytorch_collectors(monkeypatch):
    registered_hooks = []
    unregistered_hooks = []

    class WatchdogMock(object):
        @staticmethod
        def register_module_hook(module, hook):
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module, hook):
            unregistered_hooks.append((module, hook))

    class TestProfiler(profiler._ProfilerInstance):
        def _build_default_exporters(self, *args, **kargs):
            return None

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)

    p = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=True,
        _pytorch_collector_enabled=True,
    )
    p._scheduler = mock.Mock()

    p.start()
    p.stop(flush=False)

    assert len(registered_hooks) == 10
    assert [module for module, _ in registered_hooks].count("threading") == 5
    assert [module for module, _ in registered_hooks].count("asyncio") == 4
    assert [module for module, _ in registered_hooks].count("torch") == 1
    assert unregistered_hooks == registered_hooks


def test_profiler_serverless(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "foobar")
    p = profiler.Profiler()
    assert isinstance(p._scheduler, scheduler.ServerlessScheduler)
    assert p.tags["functionname"] == "foobar"


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 10), reason="ddtrace under Python 3.9 is deprecated")
@pytest.mark.subprocess()
def test_profiler_ddtrace_deprecation():
    """
    ddtrace interfaces loaded by the profiler can be marked deprecated, and we should update
    them when this happens.  As reported by https://github.com/DataDog/dd-trace-py/issues/8881
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        from ddtrace.profiling import _threading  # noqa:F401
        from ddtrace.profiling import profiler  # noqa:F401
        from ddtrace.profiling import scheduler  # noqa:F401
        from ddtrace.profiling.collector import _lock  # noqa:F401
        from ddtrace.profiling.collector import _task  # noqa:F401
        from ddtrace.profiling.collector import memalloc  # noqa:F401
        from ddtrace.profiling.collector import stack  # noqa:F401


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_ENABLED="true"),
    err="Failed to load ddup module (mock failure message), disabling profiling\n",
)
def test_libdd_failure_telemetry_logging():
    """Test that libdd initialization failures log to telemetry. This mimics
    one of the two scenarios where profiling can be configured.
    1) using ddtrace-run with DD_PROFILNG_ENABLED=true
    2) import ddtrace.profiling.auto
    """

    from unittest import mock

    with (
        mock.patch.multiple(
            "ddtrace.internal.datadog.profiling.ddup",
            failure_msg="mock failure message",
            is_available=False,
        ),
        mock.patch("ddtrace.internal.telemetry.telemetry_writer.add_log") as mock_add_log,
    ):
        from ddtrace.internal.settings.profiling import config  # noqa:F401
        from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL

        mock_add_log.assert_called_once()
        call_args = mock_add_log.call_args
        assert call_args[0][0] == TELEMETRY_LOG_LEVEL.ERROR
        message = call_args[0][1]
        assert "Failed to load ddup module" in message
        assert "mock failure message" in message


@pytest.mark.subprocess(
    # We'd like to check the stderr, but it somehow leads to triggering the
    # upload code path on macOS
    err=None
)
def test_libdd_failure_telemetry_logging_with_auto():
    from unittest import mock

    with (
        mock.patch.multiple(
            "ddtrace.internal.datadog.profiling.ddup",
            failure_msg="mock failure message",
            is_available=False,
        ),
        mock.patch("ddtrace.internal.telemetry.telemetry_writer.add_log") as mock_add_log,
    ):
        from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
        import ddtrace.profiling.auto  # noqa: F401

        mock_add_log.assert_called_once()
        call_args = mock_add_log.call_args
        assert call_args[0][0] == TELEMETRY_LOG_LEVEL.ERROR
        message = call_args[0][1]
        assert "Failed to load ddup module" in message
        assert "mock failure message" in message


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_ENABLED="true"),
    err="Failed to load stack module (mock failure message), disabling stack profiling\n",
)
def test_stack_failure_telemetry_logging():
    # Test that stack initialization failures log to telemetry. This is
    # mimicking the behavior of ddtrace-run, where the config is imported to
    # determine if profiling/stack is enabled

    from unittest import mock

    with (
        mock.patch.multiple(
            "ddtrace.internal.datadog.profiling.stack",
            failure_msg="mock failure message",
            is_available=False,
        ),
        mock.patch("ddtrace.internal.telemetry.telemetry_writer.add_log") as mock_add_log,
    ):
        from ddtrace.internal.settings.profiling import config  # noqa: F401
        from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL

        mock_add_log.assert_called_once()
        call_args = mock_add_log.call_args
        assert call_args[0][0] == TELEMETRY_LOG_LEVEL.ERROR
        message = call_args[0][1]
        assert "Failed to load stack module" in message
        assert "mock failure message" in message


@pytest.mark.subprocess(
    # We'd like to check the stderr, but it somehow leads to triggering the
    # upload code path on macOS.
    err=None,
)
def test_stack_failure_telemetry_logging_with_auto():
    from unittest import mock

    with (
        mock.patch.multiple(
            "ddtrace.internal.datadog.profiling.stack",
            failure_msg="mock failure message",
            is_available=False,
        ),
        mock.patch("ddtrace.internal.telemetry.telemetry_writer.add_log") as mock_add_log,
    ):
        from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
        import ddtrace.profiling.auto  # noqa: F401

        mock_add_log.assert_called_once()
        call_args = mock_add_log.call_args
        assert call_args[0][0] == TELEMETRY_LOG_LEVEL.ERROR
        message = call_args[0][1]
        assert "Failed to load stack module" in message
        assert "mock failure message" in message


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="only works on linux")
@pytest.mark.subprocess(err=None)
# For macOS: Could print 'Error uploading' but okay to ignore since we are checking if native_id is set
def test_user_threads_have_native_id():
    from os import getpid
    from threading import Thread
    from threading import _MainThread  # pyright: ignore[reportAttributeAccessIssue]
    from threading import current_thread
    from time import sleep

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()

    main = current_thread()
    assert isinstance(main, _MainThread)
    # We expect the current thread to have the same ID as the PID
    assert main.native_id == getpid(), (main.native_id, getpid())

    t = Thread(target=lambda: None)
    t.start()

    for _ in range(10):
        try:
            # The TID should be higher than the PID, but not too high
            assert 0 < t.native_id - getpid() < 100, (t.native_id, getpid())  # pyright: ignore[reportOptionalOperand]
        except AttributeError:
            # The native_id attribute is set by the thread so we might have to
            # wait a bit for it to be set.
            sleep(0.1)
        else:
            break
    else:
        raise AssertionError("Thread.native_id not set")

    t.join()

    p.stop()


@pytest.mark.skipif(not TESTING_GEVENT, reason="gevent is not available")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_ENABLED="false",
    )
)
def test_gevent_not_patched_when_profiling_disabled():
    import gevent

    # Import these modules to ensure that they don't have a side effect enabling
    # gevent support when profiling is disabled.
    from ddtrace.profiling import Profiler  # noqa: F401
    from ddtrace.profiling import _gevent  # noqa: F401
    from ddtrace.profiling.collector import _task  # noqa: F401

    assert gevent.spawn.__module__ != "ddtrace.profiling._gevent"
    assert gevent.spawn_later.__module__ != "ddtrace.profiling._gevent"
    assert gevent.joinall.__module__ != "ddtrace.profiling._gevent"
    assert gevent.wait.__module__ != "ddtrace.profiling._gevent"
    assert gevent.iwait.__module__ != "ddtrace.profiling._gevent"
    assert gevent.hub.spawn_raw.__module__ != "ddtrace.profiling._gevent"


@pytest.mark.skipif(not TESTING_GEVENT, reason="gevent is not available")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_ENABLED="true",
    ),
    ddtrace_run=True,
    err=None,
)
def test_gevent_patched_when_ddtrace_run_is_used():
    import gevent

    # NOTE: In this test (and the test_gevent_patched* tests below), we do not
    # assert on `gevent.Greenlet.__module__`. That check is brittle across gevent
    # internals/import aliasing and can fail even when gevent patching is active.
    # We instead assert on patched function entry points (e.g., `gevent.spawn`,
    # `gevent.wait`, `gevent.iwait`), and behavior is already covered by profiling
    # tests that validate gevent tasks are sampled.
    assert gevent.spawn.__module__ == "ddtrace.profiling._gevent"
    assert gevent.spawn_later.__module__ == "ddtrace.profiling._gevent"
    assert gevent.joinall.__module__ == "ddtrace.profiling._gevent"
    assert gevent.wait.__module__ == "ddtrace.profiling._gevent"
    assert gevent.iwait.__module__ == "ddtrace.profiling._gevent"
    assert gevent.hub.spawn_raw.__module__ == "ddtrace.profiling._gevent"


@pytest.mark.skipif(not TESTING_GEVENT, reason="gevent is not available")
@pytest.mark.subprocess(err=None)
def test_gevent_patched_when_profiling_auto():
    import gevent

    assert gevent.spawn.__module__ != "ddtrace.profiling._gevent"
    assert gevent.spawn_later.__module__ != "ddtrace.profiling._gevent"
    assert gevent.joinall.__module__ != "ddtrace.profiling._gevent"
    assert gevent.wait.__module__ != "ddtrace.profiling._gevent"
    assert gevent.iwait.__module__ != "ddtrace.profiling._gevent"
    assert gevent.hub.spawn_raw.__module__ != "ddtrace.profiling._gevent"

    import ddtrace.profiling.auto  # noqa: F401

    assert gevent.spawn.__module__ == "ddtrace.profiling._gevent"
    assert gevent.spawn_later.__module__ == "ddtrace.profiling._gevent"
    assert gevent.joinall.__module__ == "ddtrace.profiling._gevent"
    assert gevent.wait.__module__ == "ddtrace.profiling._gevent"
    assert gevent.iwait.__module__ == "ddtrace.profiling._gevent"
    assert gevent.hub.spawn_raw.__module__ == "ddtrace.profiling._gevent"


@pytest.mark.skipif(not TESTING_GEVENT, reason="gevent is not available")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_ENABLED="false",
    ),
    err=None,
)
def test_gevent_patched_after_manual_profiler_start_when_profiling_disabled():
    import gevent

    from ddtrace.profiling import profiler

    assert gevent.spawn.__module__ != "ddtrace.profiling._gevent"
    assert gevent.spawn_later.__module__ != "ddtrace.profiling._gevent"
    assert gevent.joinall.__module__ != "ddtrace.profiling._gevent"
    assert gevent.wait.__module__ != "ddtrace.profiling._gevent"
    assert gevent.iwait.__module__ != "ddtrace.profiling._gevent"
    assert gevent.hub.spawn_raw.__module__ != "ddtrace.profiling._gevent"

    p = profiler.Profiler()
    p.start()
    try:
        assert gevent.spawn.__module__ == "ddtrace.profiling._gevent"
        assert gevent.spawn_later.__module__ == "ddtrace.profiling._gevent"
        assert gevent.joinall.__module__ == "ddtrace.profiling._gevent"
        assert gevent.wait.__module__ == "ddtrace.profiling._gevent"
        assert gevent.iwait.__module__ == "ddtrace.profiling._gevent"
        assert gevent.hub.spawn_raw.__module__ == "ddtrace.profiling._gevent"
    finally:
        p.stop(flush=False)


def test_only_one_profiler_allowed(caplog: pytest.LogCaptureFixture) -> None:
    """Starting a second profiler while one is running should log an error and not start."""
    p1 = profiler.Profiler()
    p2 = profiler.Profiler()

    p1.start()
    assert profiler.Profiler._active_instance is p1

    with caplog.at_level(logging.ERROR, logger="ddtrace.profiling.profiler"):
        p2.start()

    assert "A profiler is already running" in caplog.text
    assert profiler.Profiler._active_instance is p1

    p1.stop(flush=False)


def test_stop_then_start_new_profiler() -> None:
    """After stopping the first profiler, a new one should be startable."""
    p1 = profiler.Profiler()
    p1.start()
    p1.stop(flush=False)

    assert profiler.Profiler._active_instance is None

    p2 = profiler.Profiler()
    p2.start()
    assert profiler.Profiler._active_instance is p2
    p2.stop(flush=False)


def test_same_profiler_restart_allowed() -> None:
    """Restarting the same profiler instance (stop then start) should work."""
    p = profiler.Profiler()
    p.start()
    p.stop(flush=False)
    p.start()
    assert profiler.Profiler._active_instance is p
    p.stop(flush=False)


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_ENABLED="true"),
    ddtrace_run=True,
    err=None,
)
def test_auto_profiler_blocks_manual_start():
    """When DD_PROFILING_ENABLED=1 auto-starts a profiler, manually starting another one should log an error."""
    import logging
    import logging.handlers

    from ddtrace.profiling import bootstrap
    from ddtrace.profiling import profiler

    assert hasattr(bootstrap, "profiler"), "Auto profiler should have been started by ddtrace-run"
    assert profiler.Profiler._active_instance is not None

    logger = logging.getLogger("ddtrace.profiling.profiler")
    handler = logging.handlers.MemoryHandler(capacity=100)
    logger.addHandler(handler)

    p = profiler.Profiler()
    p.start()

    error_records = [r for r in handler.buffer if r.levelno >= logging.ERROR and "already running" in r.getMessage()]
    assert len(error_records) == 1, (
        f"Expected exactly one 'already running' error, got: {[r.getMessage() for r in handler.buffer]}"
    )

    assert profiler.Profiler._active_instance is bootstrap.profiler  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="fork test only on linux")
@pytest.mark.subprocess(err=None)
def test_profiler_singleton_after_fork():
    """After fork, the child process should be able to start a new profiler."""
    import os

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    assert profiler.Profiler._active_instance is p

    pid = os.fork()
    if pid == 0:
        # Child process: the inherited _active_instance still points to the parent's profiler,
        # but after fork the service threads are dead so the status should not be RUNNING.
        # A new profiler should be startable.
        try:
            p.stop(flush=False)
            p2 = profiler.Profiler()
            p2.start()
            assert profiler.Profiler._active_instance is p2
            p2.stop(flush=False)
        except Exception as e:
            print(f"Child failed: {e}", flush=True)
            os._exit(1)
        os._exit(0)
    else:
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, f"Child exited with status {status}"
        p.stop(flush=False)
