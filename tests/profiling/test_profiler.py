import logging
import os
import sys
import time
from typing import Any
from typing import Callable
from typing import Generator
from typing import Optional
from typing import cast
from unittest import mock

import pytest

import ddtrace
from ddtrace.internal import service
from ddtrace.internal import threads as internal_threads
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.profiling import collector
from ddtrace.profiling import profiler
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import Collector
from ddtrace.profiling.collector import _lock
from ddtrace.profiling.collector import asyncio
from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT") or False


@pytest.fixture(autouse=True)
def _reset_profiler_active_instance() -> Generator[None, None, None]:
    yield
    profiler.Profiler._active_instance = None
    profiler.Profiler._exit_signal_handler = None


def test_status() -> None:
    p = profiler.Profiler()
    assert repr(p.status) == "<ServiceStatus.STOPPED: 'stopped'>"
    p.start()
    assert repr(p.status) == "<ServiceStatus.RUNNING: 'running'>"
    p.stop(flush=False)
    assert repr(p.status) == "<ServiceStatus.STOPPED: 'stopped'>"


def test_restart() -> None:
    p = profiler.Profiler()
    p.start()
    p.stop(flush=False)
    p.start()
    p.stop(flush=False)


def test_duplicate_start_does_not_stop_active_profiler():
    p = profiler.Profiler()

    with mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated") as product_activated:
        p.start()
        with pytest.raises(service.ServiceStatusError):
            p.start()

        assert p.status == service.ServiceStatus.RUNNING
        assert profiler.Profiler._active_instance is p
        product_activated.assert_called_once_with(profiler.TELEMETRY_APM_PRODUCT.PROFILER, True)
        p.stop(flush=False)


@pytest.mark.parametrize("failure_point", ["atexit", "telemetry", "signal"])
def test_start_bookkeeping_failure_rolls_back_profiler(failure_point):
    internal = mock.Mock(status=service.ServiceStatus.STOPPED)
    internal.start.side_effect = lambda: setattr(internal, "status", service.ServiceStatus.RUNNING)
    internal._rollback_start.side_effect = lambda **kwargs: setattr(internal, "status", service.ServiceStatus.STOPPED)
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register") as register,
        mock.patch("ddtrace.profiling.profiler.atexit.unregister") as unregister,
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal") as register_on_exit_signal,
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated") as product_activated,
    ):
        if failure_point == "atexit":
            register.side_effect = RuntimeError("bookkeeping failed")
        elif failure_point == "telemetry":
            product_activated.side_effect = [RuntimeError("bookkeeping failed"), None]
        else:
            register_on_exit_signal.side_effect = RuntimeError("bookkeeping failed")

        with pytest.raises(RuntimeError, match="bookkeeping failed"):
            wrapped.start()

    internal._rollback_start.assert_called_once_with(flush=False)
    unregister.assert_called_once_with(wrapped.stop)
    assert internal.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None
    if failure_point == "atexit":
        product_activated.assert_not_called()
    else:
        product_activated.assert_has_calls(
            [
                mock.call(profiler.TELEMETRY_APM_PRODUCT.PROFILER, True),
                mock.call(profiler.TELEMETRY_APM_PRODUCT.PROFILER, False),
            ]
        )


def test_start_retries_cleanup_after_bookkeeping_rollback_fails():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    test_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)
    test_scheduler.start.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.RUNNING)
    rollback_attempts = 0

    def rollback_scheduler():
        nonlocal rollback_attempts
        rollback_attempts += 1
        if rollback_attempts == 1:
            raise RuntimeError("cleanup failed")
        test_scheduler.status = service.ServiceStatus.STOPPED

    test_scheduler._rollback_start.side_effect = rollback_scheduler
    test_scheduler.stop.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.STOPPED)
    internal._scheduler = test_scheduler
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    activation_attempts = 0

    def activate_profiler(_, active):
        nonlocal activation_attempts
        if active:
            activation_attempts += 1
            if activation_attempts == 1:
                raise RuntimeError("bookkeeping failed")

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated", side_effect=activate_profiler),
    ):
        with pytest.raises(RuntimeError, match="bookkeeping failed"):
            wrapped.start()

        assert internal._start_cleanup_pending is True
        assert internal.status == service.ServiceStatus.RUNNING
        assert profiler.Profiler._active_instance is wrapped

        wrapped.start()

        assert rollback_attempts == 2
        assert internal._start_cleanup_pending is False
        assert internal.status == service.ServiceStatus.RUNNING
        assert profiler.Profiler._active_instance is wrapped
        wrapped.stop(flush=False)


def test_stop_retries_pending_cleanup_when_profiler_is_stopped():
    internal = mock.Mock(status=service.ServiceStatus.STOPPED, _start_cleanup_pending=True)
    internal.stop.side_effect = service.ServiceStatusError(internal.__class__, internal.status)

    def rollback_start(**kwargs):
        internal._start_cleanup_pending = False

    internal._rollback_start.side_effect = rollback_start
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    profiler.Profiler._active_instance = wrapped
    competing = object.__new__(profiler.Profiler)
    competing._profiler = mock.Mock(status=service.ServiceStatus.STOPPED, _start_cleanup_pending=False)

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        wrapped.stop(flush=False)
        competing.start()

    internal._rollback_start.assert_called_once_with(flush=False)
    competing._profiler.start.assert_called_once_with()
    assert internal._start_cleanup_pending is False
    assert profiler.Profiler._active_instance is competing


def test_restart_failure_rolls_back_after_pending_cleanup_changes_status():
    events = []

    class RecordingCollector(collector.Collector):
        def _start_service(self):
            events.append("collector start")

        def _stop_service(self):
            events.append("collector stop")

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    recording_collector = RecordingCollector()
    recording_collector.start()
    events.clear()
    internal._collectors = [recording_collector]
    test_scheduler = mock.Mock(status=service.ServiceStatus.RUNNING)

    def rollback_scheduler():
        events.append("scheduler rollback")
        test_scheduler.status = service.ServiceStatus.STOPPED

    def fail_scheduler_start():
        events.append("scheduler start")
        raise RuntimeError("scheduler start failed")

    test_scheduler._rollback_start.side_effect = rollback_scheduler
    test_scheduler.start.side_effect = fail_scheduler_start
    internal._scheduler = test_scheduler
    internal.status = service.ServiceStatus.RUNNING
    internal._start_cleanup_pending = True
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        pytest.raises(RuntimeError, match="scheduler start failed"),
    ):
        wrapped.start()

    assert events == [
        "scheduler rollback",
        "collector stop",
        "collector start",
        "scheduler start",
        "scheduler rollback",
        "collector stop",
    ]
    assert internal.status == service.ServiceStatus.STOPPED
    assert internal._start_cleanup_pending is False
    assert profiler.Profiler._active_instance is None


def test_multiple_stop() -> None:
    """Check that the profiler can be stopped twice."""
    p = profiler.Profiler()
    p.start()
    p.stop(flush=False)
    p.stop(flush=False)


def test_tracer_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_API_KEY", "foobar")
    prof = profiler.Profiler(tracer=ddtrace.tracer)
    assert prof.tracer == ddtrace.tracer
    for col in prof._profiler._collectors:
        if isinstance(col, stack.StackCollector):
            assert col.tracer == ddtrace.tracer
            break
    else:
        pytest.fail("Unable to find stack collector")


@pytest.mark.subprocess(env=dict(DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=None))
def test_default_memory() -> None:
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import memalloc

    mem_collectors: list[memalloc.MemoryCollector] = [
        col for col in profiler.Profiler()._profiler._collectors if isinstance(col, memalloc.MemoryCollector)
    ]
    assert mem_collectors, "MemoryCollector should be enabled by default"
    assert mem_collectors[0].mem_domain_enabled is True


@pytest.mark.subprocess(env=dict(DD_PROFILING_MEMORY_ENABLED="true"))
def test_enable_memory() -> None:
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import memalloc

    assert any(isinstance(col, memalloc.MemoryCollector) for col in profiler.Profiler()._profiler._collectors)


@pytest.mark.subprocess(env=dict(DD_PROFILING_MEMORY_ENABLED="false"))
def test_disable_memory() -> None:
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import memalloc

    assert all(not isinstance(col, memalloc.MemoryCollector) for col in profiler.Profiler()._profiler._collectors)


def test_copy() -> None:
    p = profiler._ProfilerInstance(env="123", version="dwq", service="foobar")
    c = p.copy()
    assert c == p
    assert p.env == c.env
    assert p.version == c.version
    assert p.service == c.service
    assert p.tracer == c.tracer
    assert p.tags == c.tags


def test_copy_keeps_collector_selection() -> None:
    p = profiler._ProfilerInstance(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    c = p.copy()
    for key in profiler._ProfilerInstance._COPY_PRIVATE_ATTRIBUTES:
        assert getattr(c, key) is False, key
    assert c._collectors == []


def test_profiler_does_not_mutate_custom_tags() -> None:
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self) -> None:
            self.tags["generated"] = "value"

    tags = {"team": "profiling"}
    p = TestProfiler(
        tags=tags,
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    p._scheduler = mock.Mock()
    p.start()
    p.stop(flush=False)

    assert tags == {"team": "profiling"}
    assert p.tags == {"team": "profiling", "generated": "value"}


def test_failed_start_collector(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    class ErrCollect(collector.Collector):
        def _start_service(self) -> None:
            raise RuntimeError("could not import required module")

        def _stop_service(self) -> None:
            pass

        @staticmethod
        def collect() -> None:
            pass

        @staticmethod
        def snapshot() -> None:
            raise Exception("error!")

    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kargs):
            return None

    p = TestProfiler()
    err_collector = ErrCollect()
    snapshot = mock.Mock(wraps=err_collector.snapshot)
    monkeypatch.setattr(err_collector, "snapshot", snapshot)
    p._collectors = [err_collector]
    p.start()

    def profiling_tuples(tuples: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
        return [t for t in tuples if t[0].startswith("ddtrace.profiling")]

    assert profiling_tuples(caplog.record_tuples) == [
        ("ddtrace.profiling.profiler", logging.ERROR, "Failed to start collector %r, disabling." % err_collector)
    ]
    time.sleep(2)
    p.stop()
    assert snapshot.call_count == 0
    assert profiling_tuples(caplog.record_tuples) == [
        ("ddtrace.profiling.profiler", logging.ERROR, "Failed to start collector %r, disabling." % err_collector)
    ]


def test_failed_start_collector_cleans_up_partial_resources():
    class FailingCollector(collector.Collector):
        def __init__(self):
            super().__init__()
            self.resource_active = False

        def _start_service(self):
            self.resource_active = True
            raise RuntimeError("start failed")

        def _stop_service(self):
            self.resource_active = False

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    p = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    failed_collector = FailingCollector()
    p._collectors = [failed_collector]
    p._scheduler = None

    p.start()

    assert failed_collector.resource_active is False
    assert failed_collector.status == service.ServiceStatus.STOPPED
    assert p._collectors == []
    p.stop(flush=False)


def test_cancelled_collector_start_cleans_up_partial_resources():
    class CancelledCollector(collector.Collector):
        def __init__(self):
            super().__init__()
            self.resource_active = False
            self.start_attempts = 0

        def _start_service(self):
            self.start_attempts += 1
            self.resource_active = True
            if self.start_attempts == 1:
                raise KeyboardInterrupt

        def _stop_service(self):
            self.resource_active = False

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    cancelled_collector = CancelledCollector()
    internal._collectors = [cancelled_collector]
    internal._scheduler = mock.Mock()
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with pytest.raises(KeyboardInterrupt):
        wrapped.start()

    assert cancelled_collector.resource_active is False
    assert cancelled_collector.status == service.ServiceStatus.STOPPED
    internal._scheduler.start.assert_not_called()
    assert profiler.Profiler._active_instance is None

    internal._scheduler.start.side_effect = lambda: setattr(
        internal._scheduler, "status", service.ServiceStatus.RUNNING
    )
    wrapped.start()

    assert cancelled_collector.start_attempts == 2
    assert cancelled_collector.resource_active is True
    assert cancelled_collector.status == service.ServiceStatus.RUNNING
    assert cancelled_collector in internal._collectors
    wrapped.stop(flush=False)


def test_failed_collector_cleanup_is_retried_by_profiler_rollback():
    class RetryCleanupCollector(collector.Collector):
        def __init__(self):
            super().__init__()
            self.resource_active = False
            self.stop_attempts = 0

        def _start_service(self):
            self.resource_active = True
            raise RuntimeError("start failed")

        def _stop_service(self):
            self.stop_attempts += 1
            if self.stop_attempts == 1:
                raise RuntimeError("cleanup failed")
            self.resource_active = False

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    failed_collector = RetryCleanupCollector()
    internal._collectors = [failed_collector]
    internal._scheduler = None
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with pytest.raises(RuntimeError, match="cleanup failed"):
        wrapped.start()

    assert failed_collector.stop_attempts == 2
    assert failed_collector.resource_active is False
    assert internal.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None


def test_failed_memory_collector_start_cleanup_is_retried_before_restart():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    memory_collector = memalloc.MemoryCollector()
    internal._collectors = [memory_collector]
    test_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)
    test_scheduler.start.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.RUNNING)
    test_scheduler.stop.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.STOPPED)
    internal._scheduler = test_scheduler
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    native_memalloc = mock.Mock()
    native_memalloc.start.side_effect = [RuntimeError("already started"), RuntimeError("start failed")]
    native_memalloc.stop.side_effect = [None, RuntimeError("cleanup failed"), RuntimeError("cleanup failed"), None]

    with (
        mock.patch.object(memalloc, "_memalloc", native_memalloc),
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        with pytest.raises(RuntimeError, match="cleanup failed"):
            wrapped.start()

        assert internal._collectors_pending_cleanup == [memory_collector]
        assert profiler.Profiler._active_instance is wrapped
        test_scheduler.start.assert_not_called()

        wrapped.start()
        wrapped.stop(flush=False)

    assert native_memalloc.stop.call_count == 4
    assert internal._collectors_pending_cleanup == []
    assert profiler.Profiler._active_instance is None


def test_restart_drains_pending_collector_cleanup_before_starting_scheduler():
    events = []

    class RetryCleanupCollector(collector.Collector):
        def __init__(self):
            super().__init__()
            self.resource_active = False
            self.stop_attempts = 0

        def _start_service(self):
            events.append("collector start")
            self.resource_active = True
            raise RuntimeError("start failed")

        def _stop_service(self):
            self.stop_attempts += 1
            events.append(f"collector cleanup {self.stop_attempts}")
            if self.stop_attempts < 3:
                raise RuntimeError("cleanup failed")
            self.resource_active = False

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    failed_collector = RetryCleanupCollector()
    internal._collectors = [failed_collector]
    test_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)

    def start_scheduler():
        assert failed_collector.resource_active is False
        events.append("scheduler start")
        test_scheduler.status = service.ServiceStatus.RUNNING

    test_scheduler.start.side_effect = start_scheduler
    test_scheduler.stop.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.STOPPED)
    internal._scheduler = test_scheduler
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        with pytest.raises(RuntimeError, match="cleanup failed"):
            wrapped.start()

        assert failed_collector.stop_attempts == 2
        assert failed_collector.resource_active is True
        test_scheduler.start.assert_not_called()
        assert profiler.Profiler._active_instance is wrapped

        competing = object.__new__(profiler.Profiler)
        competing._profiler = mock.Mock(status=service.ServiceStatus.STOPPED)
        competing.start()
        competing._profiler.start.assert_not_called()
        assert profiler.Profiler._active_instance is wrapped

        wrapped.start()
        wrapped.stop(flush=False)

    assert events == [
        "collector start",
        "collector cleanup 1",
        "collector cleanup 2",
        "collector cleanup 3",
        "scheduler start",
    ]
    assert failed_collector.resource_active is False
    assert internal._collectors_pending_cleanup == []


def test_restart_finishes_failed_scheduler_cleanup_before_restarting_collectors():
    events = []

    class RecordingCollector(collector.Collector):
        def _start_service(self):
            events.append("collector start")

        def _stop_service(self):
            events.append("collector stop")

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    recording_collector = RecordingCollector()
    internal._collectors = [recording_collector]
    test_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)
    scheduler_start_attempts = 0
    scheduler_cleanup_attempts = 0

    def start_scheduler():
        nonlocal scheduler_start_attempts
        scheduler_start_attempts += 1
        events.append(f"scheduler start {scheduler_start_attempts}")
        if scheduler_start_attempts == 1:
            raise RuntimeError("scheduler start failed")
        test_scheduler.status = service.ServiceStatus.RUNNING

    def cleanup_scheduler():
        nonlocal scheduler_cleanup_attempts
        scheduler_cleanup_attempts += 1
        events.append(f"scheduler cleanup {scheduler_cleanup_attempts}")
        if scheduler_cleanup_attempts == 1:
            raise RuntimeError("scheduler cleanup failed")
        test_scheduler.status = service.ServiceStatus.STOPPED

    test_scheduler.start.side_effect = start_scheduler
    test_scheduler._rollback_start.side_effect = cleanup_scheduler
    test_scheduler.stop.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.STOPPED)
    internal._scheduler = test_scheduler
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        with pytest.raises(RuntimeError, match="scheduler start failed"):
            wrapped.start()

        assert internal._start_cleanup_pending is True
        assert recording_collector.status == service.ServiceStatus.RUNNING
        assert profiler.Profiler._active_instance is wrapped

        competing = object.__new__(profiler.Profiler)
        competing._profiler = mock.Mock(status=service.ServiceStatus.STOPPED)
        competing.start()
        competing._profiler.start.assert_not_called()

        wrapped.start()
        wrapped.stop(flush=False)

    assert events == [
        "collector start",
        "scheduler start 1",
        "scheduler cleanup 1",
        "scheduler cleanup 2",
        "collector stop",
        "collector start",
        "scheduler start 2",
        "collector stop",
    ]
    assert internal._start_cleanup_pending is False
    assert profiler.Profiler._active_instance is None


def test_restart_preserves_collectors_after_failed_collector():
    events = []

    class RecordingCollector(collector.Collector):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def _start_service(self):
            events.append(f"{self.name} start")

        def _stop_service(self):
            events.append(f"{self.name} stop")

    class FailingCollector(collector.Collector):
        def __init__(self):
            super().__init__()
            self.stop_attempts = 0

        def _start_service(self):
            events.append("failed start")
            raise RuntimeError("start failed")

        def _stop_service(self):
            self.stop_attempts += 1
            events.append(f"failed cleanup {self.stop_attempts}")
            if self.stop_attempts == 1:
                raise RuntimeError("cleanup failed")

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    first = RecordingCollector("first")
    failed = FailingCollector()
    third = RecordingCollector("third")
    internal._collectors = [first, failed, third]
    test_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)
    test_scheduler.start.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.RUNNING)
    test_scheduler.stop.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.STOPPED)
    internal._scheduler = test_scheduler
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        with pytest.raises(RuntimeError, match="cleanup failed"):
            wrapped.start()

        wrapped.start()
        wrapped.stop(flush=False)

    assert events == [
        "first start",
        "failed start",
        "failed cleanup 1",
        "failed cleanup 2",
        "first stop",
        "first start",
        "third start",
        "third stop",
        "first stop",
    ]


@pytest.mark.parametrize(
    ("module_name", "collector_module", "collector_name"),
    [
        ("threading", profiler.threading, "ThreadingLockCollector"),
        ("torch", profiler.pytorch, "TorchProfilerCollector"),
    ],
)
@pytest.mark.parametrize("failure_type", [RuntimeError, KeyboardInterrupt])
def test_runtime_collector_start_failure_cleans_up_partial_resources(
    monkeypatch, module_name, collector_module, collector_name, failure_type
):
    registered_hooks = []
    instances = []

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module, hook):
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module, hook):
            pass

    class FailingCollector(collector.Collector):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.resource_active = False
            instances.append(self)

        def _start_service(self):
            self.resource_active = True
            raise failure_type("start failed")

        def _stop_service(self):
            self.resource_active = False

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)
    monkeypatch.setattr(collector_module, collector_name, FailingCollector)
    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=True,
        _pytorch_collector_enabled=True,
        _exception_profiling_enabled=False,
    )
    internal.status = service.ServiceStatus.RUNNING
    hook = next(hook for module, hook in registered_hooks if module == module_name)

    if issubclass(failure_type, Exception):
        hook(None)
    else:
        with pytest.raises(failure_type):
            hook(None)

    assert instances[-1].resource_active is False
    assert instances[-1].status == service.ServiceStatus.STOPPED
    assert instances[-1] not in internal._collectors


def test_runtime_collector_hook_does_not_duplicate_pending_cleanup(monkeypatch):
    registered_hooks = []
    instances = []

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module, hook):
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module, hook):
            pass

    class FailingCollector(collector.Collector):
        def __init__(self):
            super().__init__()
            instances.append(self)

        def _start_service(self):
            raise RuntimeError("start failed")

        def _stop_service(self):
            raise RuntimeError("cleanup failed")

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)
    monkeypatch.setattr(profiler.pytorch, "TorchProfilerCollector", FailingCollector)
    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=True,
        _exception_profiling_enabled=False,
    )
    internal.status = service.ServiceStatus.RUNNING
    hook = next(hook for module, hook in registered_hooks if module == "torch")

    hook(None)
    hook(None)

    assert len(instances) == 1
    assert internal._collectors_pending_cleanup == instances


def test_failed_start_reregisters_deferred_collector_hooks(monkeypatch):
    registered_hooks = []
    unregistered_hooks = []

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module, hook):
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module, hook):
            unregistered_hooks.append((module, hook))

    class TorchCollector(collector.Collector):
        def _start_service(self):
            pass

        def _stop_service(self):
            pass

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)
    monkeypatch.setattr(profiler.pytorch, "TorchProfilerCollector", TorchCollector)
    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=True,
        _exception_profiling_enabled=False,
    )
    start_attempts = 0
    test_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)

    def start_scheduler():
        nonlocal start_attempts
        start_attempts += 1
        if start_attempts == 1:
            raise RuntimeError("scheduler start failed")
        test_scheduler.status = service.ServiceStatus.RUNNING

    test_scheduler.start.side_effect = start_scheduler
    test_scheduler._rollback_start.side_effect = lambda: setattr(
        test_scheduler, "status", service.ServiceStatus.STOPPED
    )
    test_scheduler.stop.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.STOPPED)
    internal._scheduler = test_scheduler
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        with pytest.raises(RuntimeError, match="scheduler start failed"):
            wrapped.start()

        assert registered_hooks == [("torch", registered_hooks[0][1])]
        assert unregistered_hooks == registered_hooks

        wrapped.start()
        assert registered_hooks == [("torch", registered_hooks[0][1]), ("torch", registered_hooks[0][1])]
        registered_hooks[-1][1](None)

        assert any(type(col) is TorchCollector for col in internal._collectors)
        wrapped.stop(flush=False)


def test_failed_lock_collector_start_preserves_patch_target():
    original_lock = profiler.threading.ThreadingLockCollector.MODULE.Lock

    class FailingLockCollector(profiler.threading.ThreadingLockCollector):
        def patch(self):
            raise RuntimeError("patch failed")

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    internal._collectors = [FailingLockCollector()]
    internal._scheduler = None

    try:
        internal.start()
        installed_lock = profiler.threading.ThreadingLockCollector.MODULE.Lock
    finally:
        profiler.threading.ThreadingLockCollector.MODULE.Lock = original_lock

    assert installed_lock is original_lock
    internal.stop(flush=False)


def test_failed_pytorch_collector_start_preserves_patch_target():
    original_target = object()

    class FailingMLCollector(profiler.pytorch.MLProfilerCollector):
        PROFILED_TORCH_CLASS = object

        def __init__(self):
            super().__init__()
            self.target = original_target

        def _get_patch_target(self):
            raise RuntimeError("patch failed")

        def _set_patch_target(self, value):
            self.target = value

        def _start_service(self):
            self.patch()

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    failed_collector = FailingMLCollector()
    internal._collectors = [failed_collector]
    internal._scheduler = None

    internal.start()

    assert failed_collector.target is original_target
    internal.stop(flush=False)


def test_partial_start_rollback_stops_components():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    p = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)
    started_collector = mock.Mock(status=service.ServiceStatus.RUNNING)
    p._scheduler = scheduler
    p._collectors = [started_collector]

    p._rollback_start(flush=True)

    scheduler._rollback_start.assert_called_once_with()
    scheduler.stop.assert_not_called()
    scheduler.join.assert_called_once_with()
    scheduler.flush.assert_called_once_with()
    started_collector.stop.assert_called_once_with()
    started_collector.join.assert_called_once_with()


def test_partial_start_rollback_finalizes_failed_stop():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated") as product_activated,
        mock.patch("ddtrace.profiling.profiler.ddup.upload"),
    ):
        wrapped.start()
        assert internal.status == service.ServiceStatus.RUNNING
        assert internal._scheduler.status == service.ServiceStatus.RUNNING

        original_stop_service = internal._scheduler._stop_service
        stop_attempts = 0

        def fail_once(*args, **kwargs):
            nonlocal stop_attempts
            stop_attempts += 1
            if stop_attempts == 1:
                raise RuntimeError("stop failed")
            return original_stop_service(*args, **kwargs)

        with mock.patch.object(internal._scheduler, "_stop_service", side_effect=fail_once):
            with pytest.raises(RuntimeError, match="stop failed"):
                wrapped.stop()

    assert internal.status == service.ServiceStatus.STOPPED
    assert internal._scheduler.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None
    product_activated.assert_has_calls(
        [
            mock.call(profiler.TELEMETRY_APM_PRODUCT.PROFILER, True),
            mock.call(profiler.TELEMETRY_APM_PRODUCT.PROFILER, False),
        ]
    )


def test_stop_releases_ownership_after_failed_deferred_scheduler_start(monkeypatch):
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    internal._collectors = []
    internal._scheduler = scheduler.Scheduler()
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    deferred_starts = []
    monkeypatch.setattr(internal_threads, "_forking", True)
    monkeypatch.setattr(internal_threads, "_threads_to_start_after_fork", deferred_starts)

    class FailedRestart:
        name = "failed restart"

        def start(self):
            raise OSError("thread creation failed")

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        wrapped.start()
        assert profiler.Profiler._active_instance is wrapped
        assert deferred_starts

        deferred_starts[:] = [FailedRestart().start]
        internal_threads._after_fork_child()

        with pytest.raises(RuntimeError, match="Thread not started"):
            wrapped.stop(flush=False)

    assert deferred_starts == []
    assert internal._scheduler._worker is None
    assert internal._scheduler.status == service.ServiceStatus.STOPPED
    assert internal.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None


def test_stop_retry_skips_scheduler_that_already_stopped():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    test_scheduler = mock.Mock(status=service.ServiceStatus.RUNNING)

    def stop_scheduler():
        if test_scheduler.status == service.ServiceStatus.STOPPED:
            raise service.ServiceStatusError(type(test_scheduler), test_scheduler.status)
        test_scheduler.status = service.ServiceStatus.STOPPED

    test_scheduler.stop.side_effect = stop_scheduler
    test_scheduler.join.side_effect = [RuntimeError("join failed"), RuntimeError("join failed")]
    internal._scheduler = test_scheduler
    internal._collectors = []
    internal.status = service.ServiceStatus.RUNNING
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    profiler.Profiler._active_instance = wrapped

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated") as product_activated,
    ):
        with pytest.raises(RuntimeError, match="join failed"):
            wrapped.stop()

        assert internal.status == service.ServiceStatus.RUNNING
        assert profiler.Profiler._active_instance is wrapped
        test_scheduler.join.side_effect = None
        wrapped.stop()

    assert test_scheduler.stop.call_count == 1
    test_scheduler._rollback_start.assert_not_called()
    assert internal.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None
    product_activated.assert_called_once_with(profiler.TELEMETRY_APM_PRODUCT.PROFILER, False)


def test_signal_stop_recovers_failed_scheduler_transition():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    test_scheduler = mock.Mock(status=service.ServiceStatus.RUNNING)
    test_scheduler.stop.side_effect = RuntimeError("stop failed")
    test_scheduler._rollback_start.side_effect = lambda: setattr(
        test_scheduler, "status", service.ServiceStatus.STOPPED
    )
    internal._scheduler = test_scheduler
    internal._collectors = []
    internal.status = service.ServiceStatus.RUNNING
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    profiler.Profiler._active_instance = wrapped

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated") as product_activated,
    ):
        wrapped._stop_on_signal()

    test_scheduler._rollback_start.assert_called_once_with()
    test_scheduler.join.assert_called_once_with()
    test_scheduler.flush.assert_called_once_with()
    assert internal.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None
    product_activated.assert_called_once_with(profiler.TELEMETRY_APM_PRODUCT.PROFILER, False)


def test_signal_stop_preserves_ownership_when_cleanup_fails():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    test_scheduler = mock.Mock(status=service.ServiceStatus.RUNNING)
    test_scheduler.stop.side_effect = RuntimeError("stop failed")
    test_scheduler._rollback_start.side_effect = RuntimeError("cleanup failed")
    internal._scheduler = test_scheduler
    internal._collectors = []
    internal.status = service.ServiceStatus.RUNNING
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    profiler.Profiler._active_instance = wrapped

    competing = object.__new__(profiler.Profiler)
    competing._profiler = mock.Mock(status=service.ServiceStatus.STOPPED)

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated") as product_activated,
    ):
        wrapped._stop_on_signal()
        competing.start()

    assert internal._start_cleanup_pending is True
    assert profiler.Profiler._active_instance is wrapped
    competing._profiler.start.assert_not_called()
    product_activated.assert_not_called()


def test_constructor_failure_rolls_back_partial_instance(monkeypatch):
    registered_hooks = []
    unregistered_hooks = []
    partial_instances = []

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module, hook):
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module, hook):
            unregistered_hooks.append((module, hook))

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

        def __post_init__(self):
            super().__post_init__()
            partial_instances.append(self)
            raise RuntimeError("late constructor failure")

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)
    with (
        mock.patch.object(profiler.ddup, "upload") as upload,
        pytest.raises(RuntimeError, match="late constructor failure"),
    ):
        TestProfiler(
            _memory_collector_enabled=False,
            _stack_collector_enabled=False,
            _lock_collector_enabled=True,
            _pytorch_collector_enabled=True,
            _exception_profiling_enabled=False,
        )

    assert registered_hooks
    assert unregistered_hooks == registered_hooks
    assert partial_instances[0]._collectors_on_import_armed == []
    upload.assert_not_called()


def test_import_hook_registration_failure_unregisters_installed_hook(monkeypatch):
    registered_hooks = []
    unregistered_hooks = []

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module, hook):
            registered_hooks.append((module, hook))
            raise RuntimeError("hook callback failed")

        @staticmethod
        def unregister_module_hook(module, hook):
            unregistered_hooks.append((module, hook))

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)

    with pytest.raises(RuntimeError, match="hook callback failed"):
        TestProfiler(
            _memory_collector_enabled=False,
            _stack_collector_enabled=False,
            _lock_collector_enabled=False,
            _pytorch_collector_enabled=True,
            _exception_profiling_enabled=False,
        )

    assert len(registered_hooks) == 1
    assert unregistered_hooks == registered_hooks


def test_restart_replaces_import_hook_collector_after_pending_cleanup(monkeypatch):
    instances = []

    class RetryCleanupCollector(collector.Collector):
        def __init__(self):
            super().__init__()
            self.stop_attempts = 0
            instances.append(self)

        def _start_service(self):
            if self is instances[0]:
                raise RuntimeError("start failed")

        def _stop_service(self):
            self.stop_attempts += 1
            if self is instances[0] and self.stop_attempts < 3:
                raise RuntimeError("cleanup failed")

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module, hook):
            hook(None)

        @staticmethod
        def unregister_module_hook(module, hook):
            return None

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)
    monkeypatch.setattr(profiler.pytorch, "TorchProfilerCollector", RetryCleanupCollector)
    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=True,
        _exception_profiling_enabled=False,
    )
    test_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)
    test_scheduler.start.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.RUNNING)
    test_scheduler.stop.side_effect = lambda: setattr(test_scheduler, "status", service.ServiceStatus.STOPPED)
    internal._scheduler = test_scheduler
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        with pytest.raises(RuntimeError, match="cleanup failed"):
            wrapped.start()

        assert len(instances) == 1
        assert internal._collectors_pending_cleanup == [instances[0]]

        wrapped.start()

        assert len(instances) == 2
        assert instances[1].status == service.ServiceStatus.RUNNING
        wrapped.stop(flush=False)

    assert instances[0].stop_attempts == 3
    assert internal._collectors_pending_cleanup == []


@pytest.mark.parametrize("start_method", ["start", "_start_on_fork"])
def test_failed_profiler_start_rolls_back_started_components(start_method):
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    started_collector = mock.Mock(status=service.ServiceStatus.STOPPED)
    started_collector.start.side_effect = lambda: setattr(started_collector, "status", service.ServiceStatus.RUNNING)
    started_collector.stop.side_effect = lambda: setattr(started_collector, "status", service.ServiceStatus.STOPPED)
    failed_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)
    failed_scheduler.start.side_effect = RuntimeError("scheduler start failed")
    internal._collectors = [started_collector]
    internal._scheduler = failed_scheduler
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.atexit.register_on_exit_signal"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated") as product_activated,
        pytest.raises(RuntimeError, match="scheduler start failed"),
    ):
        getattr(wrapped, start_method)()

    assert internal.status == service.ServiceStatus.STOPPED
    assert started_collector.status == service.ServiceStatus.STOPPED
    started_collector.stop.assert_called_once_with()
    failed_scheduler._rollback_start.assert_called_once_with()
    assert profiler.Profiler._active_instance is None
    product_activated.assert_not_called()


def test_failed_fork_start_reserves_profiler_until_cleanup_retry():
    internal = mock.Mock(status=service.ServiceStatus.STOPPED)
    internal.start.side_effect = RuntimeError("start failed")
    internal._rollback_start.side_effect = [RuntimeError("cleanup failed"), None]
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    with pytest.raises(RuntimeError, match="start failed"):
        wrapped._start_on_fork()

    assert profiler.Profiler._active_instance is wrapped

    competing = object.__new__(profiler.Profiler)
    competing._profiler = mock.Mock(status=service.ServiceStatus.STOPPED)
    competing._start_on_fork()
    competing._profiler.start.assert_not_called()
    assert profiler.Profiler._active_instance is wrapped

    with profiler.Profiler._active_lock:
        wrapped._rollback_start_with_active_lock()

    assert internal._rollback_start.call_count == 2
    assert profiler.Profiler._active_instance is None


def test_partial_start_rollback_preserves_collectors_until_scheduler_stops():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    failed_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)
    failed_scheduler._rollback_start.side_effect = RuntimeError("scheduler cleanup failed")
    first_collector = mock.Mock()
    first_collector.stop.side_effect = RuntimeError("collector cleanup failed")
    second_collector = mock.Mock()
    internal._scheduler = failed_scheduler
    internal._collectors = [second_collector, first_collector]

    with pytest.raises(RuntimeError, match="scheduler cleanup failed"):
        internal._rollback_start(flush=True)

    failed_scheduler.join.assert_not_called()
    failed_scheduler.flush.assert_not_called()
    first_collector.stop.assert_not_called()
    second_collector.stop.assert_not_called()
    first_collector.join.assert_not_called()
    second_collector.join.assert_not_called()


def test_stop_preserves_collectors_until_scheduler_stops():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    test_scheduler = mock.Mock(status=service.ServiceStatus.RUNNING)
    test_scheduler.stop.side_effect = RuntimeError("scheduler stop failed")
    test_collector = mock.Mock()
    internal._scheduler = test_scheduler
    internal._collectors = [test_collector]
    internal.status = service.ServiceStatus.RUNNING

    with pytest.raises(RuntimeError, match="scheduler stop failed"):
        internal._stop_service(flush=False)

    test_collector.stop.assert_not_called()
    test_collector.join.assert_not_called()

    def stop_scheduler():
        test_scheduler.status = service.ServiceStatus.STOPPED

    test_scheduler.stop.side_effect = stop_scheduler
    internal._stop_service(flush=False)

    test_collector.stop.assert_called_once_with()
    test_collector.join.assert_called_once_with()


def test_stop_does_not_join_collector_whose_stop_failed():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    failed_collector = mock.Mock()
    failed_collector.stop.side_effect = RuntimeError("collector stop failed")
    stopped_collector = mock.Mock()
    internal._scheduler = None
    internal._collectors = [stopped_collector, failed_collector]

    with pytest.raises(RuntimeError, match="collector stop failed"):
        internal._stop_service(flush=False)

    failed_collector.join.assert_not_called()
    stopped_collector.join.assert_called_once_with()

    failed_collector.stop.side_effect = None
    internal._stop_service(flush=False)

    failed_collector.join.assert_called_once_with()


def test_stop_retry_does_not_flush_twice_after_collector_failure():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    events = []
    test_scheduler = mock.Mock(status=service.ServiceStatus.RUNNING)

    def stop_scheduler():
        events.append("scheduler stop")
        test_scheduler.status = service.ServiceStatus.STOPPED

    test_scheduler.stop.side_effect = stop_scheduler
    test_scheduler.flush.side_effect = lambda: events.append("flush")
    test_collector = mock.Mock()
    collector_stop_attempts = 0

    def stop_collector():
        nonlocal collector_stop_attempts
        collector_stop_attempts += 1
        if collector_stop_attempts == 1:
            raise RuntimeError("collector stop failed")
        events.append("collector stop")

    test_collector.stop.side_effect = stop_collector
    internal._scheduler = test_scheduler
    internal._collectors = [test_collector]
    internal.status = service.ServiceStatus.RUNNING
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    profiler.Profiler._active_instance = wrapped

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
        pytest.raises(RuntimeError, match="collector stop failed"),
    ):
        wrapped.stop()

    assert events == ["scheduler stop", "flush", "collector stop"]
    test_scheduler.flush.assert_called_once_with()
    assert test_collector.stop.call_count == 2
    assert internal.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None


def test_stop_does_not_retry_flush_after_upload_may_have_succeeded():
    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    class TestCollector(collector.Collector):
        def _start_service(self):
            pass

        def _stop_service(self):
            events.append("collector stop")

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    events = []
    flush_error = RuntimeError("flush failed")
    test_scheduler = mock.Mock(status=service.ServiceStatus.RUNNING)

    def stop_scheduler():
        events.append("scheduler stop")
        test_scheduler.status = service.ServiceStatus.STOPPED

    def flush_scheduler():
        events.append("upload")
        raise flush_error

    test_scheduler.stop.side_effect = stop_scheduler
    test_scheduler.flush.side_effect = flush_scheduler
    test_collector = TestCollector()
    test_collector.status = service.ServiceStatus.RUNNING
    internal._scheduler = test_scheduler
    internal._collectors = [test_collector]
    internal.status = service.ServiceStatus.RUNNING
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    profiler.Profiler._active_instance = wrapped

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
        pytest.raises(RuntimeError, match="flush failed") as raised,
    ):
        wrapped.stop()

    assert raised.value is flush_error
    assert events == ["scheduler stop", "upload", "collector stop"]
    test_scheduler.flush.assert_called_once_with()
    assert test_collector.status == service.ServiceStatus.STOPPED
    assert internal.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None


def test_default_collectors() -> None:
    p = profiler.Profiler()
    p.start()
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


def test_stop_retries_failed_import_hook_cleanup(monkeypatch):
    hook = mock.Mock()
    unregister_attempts = 0

    class WatchdogMock:
        @staticmethod
        def unregister_module_hook(module, registered_hook):
            nonlocal unregister_attempts
            unregister_attempts += 1
            if unregister_attempts == 1:
                raise RuntimeError("unregister failed")
            assert module == "threading"
            assert registered_hook is hook

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)
    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    internal._collectors_on_import = [("threading", hook)]
    internal._collectors_on_import_armed = [("threading", hook)]
    internal._scheduler = None
    internal.status = service.ServiceStatus.RUNNING
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal
    profiler.Profiler._active_instance = wrapped

    with (
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated") as product_activated,
        pytest.raises(RuntimeError, match="unregister failed"),
    ):
        wrapped.stop(flush=False)

    assert unregister_attempts == 2
    assert internal._collectors_on_import_armed == []
    assert internal.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None
    product_activated.assert_called_once_with(profiler.TELEMETRY_APM_PRODUCT.PROFILER, False)


def test_stop_unregisters_pytorch_hook_when_lock_collector_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    registered_hooks = []
    unregistered_hooks = []

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module: str, hook: Callable[[Any], None]) -> None:
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module: str, hook: Callable[[Any], None]) -> None:
            unregistered_hooks.append((module, hook))

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kargs):
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


def test_stop_unregisters_all_import_hooks_for_lock_and_pytorch_collectors(monkeypatch: pytest.MonkeyPatch) -> None:
    registered_hooks = []
    unregistered_hooks = []

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module: str, hook: Callable[[Any], None]) -> None:
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module: str, hook: Callable[[Any], None]) -> None:
            unregistered_hooks.append((module, hook))

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kargs):
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


@pytest.mark.parametrize("pytorch_enabled", [False, True])
def test_lock_collectors_keep_their_tracer(pytorch_enabled: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    # Use a mock ModuleWatchdog to simulate the delayed import of threading/asyncio.
    # This is needed because in practice, when running the test suite, both threading and asyncio
    # have already been imported by the time the profiler starts.
    registered_hooks: list[tuple[str, Callable[[Any], None]]] = []

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module: str, hook: Callable[[Any], None]) -> None:
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module: str, hook: Callable[[Any], None]) -> None:
            pass

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)

    p = profiler.Profiler(_pytorch_collector_enabled=pytorch_enabled)
    # Hooks are armed on start, not at construction.
    p.start()
    try:
        for module, hook in registered_hooks:
            if module in ("threading", "asyncio"):
                hook(None)

        locks = [c for c in p._profiler._collectors if isinstance(c, _lock.LockCollector)]
        assert locks, "expected lock collectors"
        missing = sorted({type(c).__name__ for c in locks if c.tracer is None})
        assert not missing, "lock collectors built without a tracer: %s" % missing
    finally:
        p.stop(flush=False)


def test_start_does_not_half_start_when_a_collector_cannot_be_built() -> None:
    with mock.patch.object(threading.ThreadingLockCollector, "__init__", side_effect=RuntimeError("boom")):
        p1 = profiler.Profiler()
        p1.start()
        try:
            assert profiler.Profiler._active_instance is p1, (
                "a started profiler must be recorded even if a collector could not be built"
            )
        finally:
            p1.stop(flush=False)

    assert profiler.Profiler._active_instance is None


def test_restart_rearms_collector_import_hooks() -> None:
    p = profiler.Profiler()
    inst = p._profiler
    hooks = list(inst._collectors_on_import or [])
    assert hooks, "expected lock collector import hooks to be configured"

    def watched() -> set[str]:
        hook_map = cast(ModuleWatchdog, ModuleWatchdog._instance)._hook_map
        return {module for module, hook in hooks if any(h is hook for h in hook_map.get(module, []))}

    p.start()
    assert watched() == {"threading", "asyncio"}

    p.stop(flush=False)
    assert watched() == set(), "hooks must be disarmed while the profiler is stopped"

    p.start()
    assert watched() == {"threading", "asyncio"}, "restart must re-arm the import hooks"

    p.stop(flush=False)
    assert watched() == set()


def test_unstarted_profiler_registers_no_import_hooks() -> None:
    p = profiler.Profiler()
    hooks = list(p._profiler._collectors_on_import or [])
    assert hooks, "expected lock collector import hooks to be configured"

    def registered() -> set[str]:
        # Count this profiler's own hooks by identity. A total over _hook_map would also
        # pick up the one-time process-wide hooks that the first lock collector and the
        # stack collector register (gevent.monkey, faulthandler), which never come back
        # off and would make this depend on what ran earlier in the process.
        hook_map = cast(ModuleWatchdog, ModuleWatchdog._instance)._hook_map
        return {module for module, hook in hooks if any(h is hook for h in hook_map.get(module, []))}

    assert registered() == set(), "building a profiler must not register its import hooks"

    p.start()
    assert registered() == {module for module, _ in hooks}
    p.stop(flush=False)
    assert registered() == set()


@pytest.mark.subprocess(err=None)
def test_late_imported_module_gets_its_collector_after_restart() -> None:
    import sys

    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import asyncio as asyncio_collector

    ASYNCIO_COLLECTORS = (
        asyncio_collector.AsyncioLockCollector,
        asyncio_collector.AsyncioSemaphoreCollector,
        asyncio_collector.AsyncioBoundedSemaphoreCollector,
        asyncio_collector.AsyncioConditionCollector,
    )

    def run(restart: bool) -> int:
        # Drop asyncio so its hooks have something to fire on later, the way torch shows
        # up only once the application imports it.
        for name in [m for m in sys.modules if m == "asyncio" or m.startswith("asyncio.")]:
            del sys.modules[name]

        p = profiler.Profiler()
        inst = p._profiler
        assert not [c for c in inst._collectors if isinstance(c, ASYNCIO_COLLECTORS)]

        p.start()
        if restart:
            p.stop(flush=False)
            p.start()

        import asyncio  # noqa: F401

        found = len([c for c in inst._collectors if isinstance(c, ASYNCIO_COLLECTORS)])
        p.stop(flush=False)
        profiler.Profiler._active_instance = None
        return found

    assert run(restart=False) == 4
    assert run(restart=True) == 4, "a restarted profiler must still pick up a late import"


def test_stop_completes_when_a_collector_fails_to_stop(caplog: pytest.LogCaptureFixture) -> None:
    class BadCollector:
        def start(self) -> None:
            pass

        def stop(self) -> None:
            raise RuntimeError("collector teardown blew up")

        def join(self, timeout: Optional[float] = None) -> None:
            pass

        def snapshot(self) -> None:
            pass

    p1 = profiler.Profiler()
    p1.start()
    inst = p1._profiler

    real = list(inst._collectors)
    assert real, "expected the profiler to have collectors"
    # Last in the list, so reversed() reaches it before any of the real ones.
    inst._collectors = real + [cast(Collector, BadCollector())]

    with caplog.at_level(logging.ERROR, logger="ddtrace.profiling.profiler"):
        p1.stop(flush=False)

    assert inst.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None
    for col in real:
        status = getattr(col, "status", None)
        if status is not None:
            assert status == service.ServiceStatus.STOPPED, "%r was left running" % col

    assert any("Error while stopping collector" in m for m in caplog.messages)

    p2 = profiler.Profiler()
    p2.start()
    assert profiler.Profiler._active_instance is p2
    p2.stop(flush=False)


def test_stop_skips_scheduler_join_when_scheduler_fails_to_stop(caplog: pytest.LogCaptureFixture) -> None:
    p = profiler.Profiler()
    p.start()
    inst = p._profiler
    sched = inst._scheduler
    assert sched is not None

    real = list(inst._collectors)
    try:
        with mock.patch.object(sched, "stop", side_effect=RuntimeError("scheduler stop blew up")):
            with mock.patch.object(sched, "join") as join_mock:
                with caplog.at_level(logging.ERROR, logger="ddtrace.profiling.profiler"):
                    p.stop(flush=False)

        join_mock.assert_not_called()
        assert any("Error while stopping the profile scheduler" in m for m in caplog.messages)
        assert inst.status == service.ServiceStatus.STOPPED
        for col in real:
            status = getattr(col, "status", None)
            if status is not None:
                assert status == service.ServiceStatus.STOPPED, "%r was left running" % col
    finally:
        sched.stop()
        sched.join()


def test_profiler_serverless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "foobar")
    p = profiler.Profiler()
    assert isinstance(p._scheduler, scheduler.ServerlessScheduler)

    # Tags are applied on Profiler start, so start and stop it.
    p.start()
    p.stop(flush=False)
    assert p.tags["functionname"] == "foobar"


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 10), reason="ddtrace under Python 3.9 is deprecated")
@pytest.mark.subprocess()
def test_profiler_ddtrace_deprecation() -> None:
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
def test_libdd_failure_telemetry_logging() -> None:
    """Test that libdd initialization failures log to telemetry. This mimics
    one of the two scenarios where profiling can be configured.
    1) using ddtrace-run with DD_PROFILING_ENABLED=true
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
def test_libdd_failure_telemetry_logging_with_auto() -> None:
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
def test_stack_failure_telemetry_logging() -> None:
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
def test_stack_failure_telemetry_logging_with_auto() -> None:
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


@pytest.mark.subprocess(err=None)
def test_profiling_auto_degrades_when_unavailable() -> None:
    """import ddtrace.profiling.auto must not crash when native extensions are missing."""
    import sys

    import ddtrace.profiling as profiling_pkg

    profiling_pkg.is_available = False
    profiling_pkg.failure_msg = "native extensions missing"
    sys.modules.pop("ddtrace.profiling.bootstrap.sitecustomize", None)
    sys.modules.pop("ddtrace.profiling.auto", None)

    import ddtrace.profiling.auto  # noqa: F401


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="only works on linux")
@pytest.mark.subprocess(err=None)
# For macOS: Could print 'Error uploading' but okay to ignore since we are checking if native_id is set
def test_user_threads_have_native_id() -> None:
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
def test_gevent_not_patched_when_profiling_disabled() -> None:
    import gevent
    import gevent.hub

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
def test_gevent_patched_when_ddtrace_run_is_used() -> None:
    import gevent
    import gevent.hub

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
def test_gevent_patched_when_profiling_auto() -> None:
    import gevent
    import gevent.hub

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
def test_gevent_patched_after_manual_profiler_start_when_profiling_disabled() -> None:
    import gevent
    import gevent.hub

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
    p2.stop(flush=False)  # type: ignore[unreachable]


def test_same_profiler_restart_allowed() -> None:
    """Restarting the same profiler instance (stop then start) should work."""
    p = profiler.Profiler()
    p.start()
    p.stop(flush=False)
    p.start()
    assert profiler.Profiler._active_instance is p
    p.stop(flush=False)


def test_stop_completes_teardown_when_final_upload_fails() -> None:
    p1 = profiler.Profiler()
    p1.start()

    with mock.patch.object(ddup, "upload", side_effect=RuntimeError("upload failed")):
        p1.stop(flush=True)

    assert p1.status == service.ServiceStatus.STOPPED
    assert profiler.Profiler._active_instance is None

    p2 = profiler.Profiler()
    p2.start()
    assert profiler.Profiler._active_instance is p2
    p2.stop(flush=False)


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_no_samples_pushed_after_stop",
        # Long enough that the scheduler never flushes on its own, so the only two uploads
        # are the one Profiler.stop() makes and the one this test forces at the end.
        DD_PROFILING_UPLOAD_INTERVAL="600",
        # Capture every lock event, so the pre-stop sanity check below does not hinge on the
        # default 1% sampling happening to pick up one of our acquires.
        DD_PROFILING_CAPTURE_PCT="100",
    ),
    err=None,
)
def test_no_samples_pushed_after_stop() -> None:
    """Stopping a Profiler must stop every collector from pushing samples to libdatadog."""
    import os
    import threading
    import time
    from typing import Any

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    # Duration of each of the two work phases of test_no_samples_pushed_after_stop. The stack
    # sampler and the lock collector both need a little wall clock to produce samples, and the
    # post-stop phase needs the same budget for the absence of samples to mean anything.
    _STOP_TEST_WORK_DURATION = 2.0

    def _burn_cpu_and_lock(lock: Any) -> None:
        deadline = time.monotonic() + _STOP_TEST_WORK_DURATION
        while time.monotonic() < deadline:
            with lock:
                sum(range(1000))

    # The two phases of test_no_samples_pushed_after_stop call the same work through differently
    # named wrappers, so a sample can be attributed to a phase by the frame it carries.
    def while_profiler_is_running(lock: Any) -> None:
        _burn_cpu_and_lock(lock)

    def after_profiler_is_stopped(lock: Any) -> None:
        _burn_cpu_and_lock(lock)

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_filename = pprof_prefix + "." + str(os.getpid())

    p = profiler.Profiler()
    p.start()

    # Allocated while profiling is on, so the lock collector wraps it, and reused in the
    # post-stop phase: a wrapped lock that outlives the profiler must go quiet as well.
    lock = threading.Lock()
    while_profiler_is_running(lock)

    p.stop()

    profile = pprof_utils.parse_newest_profile(output_filename)
    for value_type in ("wall-time", "lock-acquire"):
        samples = pprof_utils.get_samples_with_value_type(profile, value_type)
        assert pprof_utils.get_samples_with_function(profile, samples, "while_profiler_is_running"), (
            f"No {value_type} sample reached libdatadog while the profiler was running, so this "
            "test cannot tell a stopped profiler apart from one that never sampled"
        )

    after_profiler_is_stopped(lock)

    # Flush whatever reached libdatadog since the profiler stopped. Nothing should have.
    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename, assert_samples=False)
    leaked = pprof_utils.get_samples_with_function(profile, profile.sample, "after_profiler_is_stopped")
    assert not leaked, (
        f"{len(leaked)} sample(s) were pushed to libdatadog after the profiler was stopped: "
        + ", ".join(
            sorted(
                {
                    pprof_utils.get_location_from_id(profile, location_id).function_name
                    for sample in leaked
                    for location_id in sample.location_id
                }
            )
        )
    )


def test_construction_does_not_configure_global_exporter() -> None:
    with (
        mock.patch.object(ddup, "config") as mock_config,
        mock.patch.object(ddup, "start") as mock_start,
    ):
        p = profiler.Profiler(service="built-but-not-started")
        mock_config.assert_not_called()
        mock_start.assert_not_called()

        p.start()
        try:
            mock_config.assert_called_once()
            assert mock_config.call_args.kwargs["service"] == "built-but-not-started"
            mock_start.assert_called_once()
        finally:
            p.stop(flush=False)


def test_refused_profiler_does_not_reconfigure_running_instance() -> None:
    p1 = profiler.Profiler(service="running-profiler")
    p1.start()
    try:
        with mock.patch.object(ddup, "config") as mock_config:
            p2 = profiler.Profiler(service="refused-profiler")
            p2.start()

            assert profiler.Profiler._active_instance is p1
            mock_config.assert_not_called()
    finally:
        p1.stop(flush=False)


def test_construction_does_not_enable_endpoint_collection() -> None:
    endpoint_processor = ddtrace.tracer._endpoint_call_counter_span_processor

    with mock.patch.object(endpoint_processor, "enable") as mock_enable:
        p = profiler.Profiler(endpoint_collection_enabled=True)
        mock_enable.assert_not_called()

        p.start()
        try:
            mock_enable.assert_called_once()
        finally:
            p.stop(flush=False)


@pytest.mark.subprocess(err=None)
def test_start_registers_sigterm_handler_once_per_process() -> None:
    from unittest import mock

    from ddtrace.internal import atexit
    from ddtrace.profiling import profiler

    with mock.patch.object(atexit, "register_on_exit_signal", wraps=atexit.register_on_exit_signal) as mock_reg:
        p1 = profiler.Profiler()
        p1.start()
        mock_reg.assert_called_once_with(profiler.Profiler._stop_active_instance_on_signal)
        assert profiler.Profiler._exit_signal_handler is not None
        p1.stop(flush=False)

        p1.start()
        p1.stop(flush=False)
        p2 = profiler.Profiler()
        p2.start()
        p2.stop(flush=False)

        assert mock_reg.call_count == 1, "the exit signal handler must be registered once per process"


@pytest.mark.subprocess(err=None)
def test_exit_signal_handler_does_not_retain_stopped_profilers() -> None:
    """Restarting profiling must not pin every profiler that has already run.

    Regression test: start registered a handler bound to that profiler, and the chain
    those handlers form is never unwound, so each stopped profiler stayed reachable
    together with all of its collectors.
    """
    import gc
    import weakref

    from ddtrace.profiling import profiler

    refs = []
    for _ in range(3):
        p = profiler.Profiler()
        p.start()
        p.stop(flush=False)
        refs.append(weakref.ref(p._profiler))
        del p

    for _ in range(3):
        gc.collect()

    alive = [r for r in refs if r() is not None]
    assert not alive, "%d of %d stopped profilers were retained" % (len(alive), len(refs))


@pytest.mark.subprocess(err=None)
def test_exit_signal_handler_targets_the_active_profiler() -> None:
    """The exit signal handler must act on the running profiler, not on a stopped one."""
    from ddtrace.profiling import profiler

    stopped = profiler.Profiler()
    stopped.start()
    stopped.stop(flush=False)

    running = profiler.Profiler()
    running.start()

    called = []
    stopped._stop_on_signal = lambda: called.append("stopped")  # type: ignore[method-assign]
    running._stop_on_signal = lambda: called.append("running")  # type: ignore[method-assign]

    profiler.Profiler._stop_active_instance_on_signal()
    assert called == ["running"]

    running.stop(flush=False)

    # With nothing active the handler is a no-op rather than a flush of a dead profiler.
    called.clear()
    profiler.Profiler._stop_active_instance_on_signal()
    assert called == []


def test_internal_start_can_skip_sigterm_handler() -> None:
    from unittest import mock

    from ddtrace.internal import atexit
    from ddtrace.profiling import profiler

    with mock.patch.object(atexit, "register_on_exit_signal") as mock_reg:
        p = profiler.Profiler()
        with profiler.Profiler._active_lock:
            p._start_with_active_lock(register_on_exit_signal=False)
        mock_reg.assert_not_called()
        p.stop(flush=False)


def test_signal_stop_skips_profiler_lifecycle_owned_by_current_thread() -> None:
    p = object.__new__(profiler.Profiler)
    p._profiler = mock.Mock()

    with profiler.Profiler._active_lock:
        p._stop_on_signal()

    p._profiler.stop.assert_not_called()


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not supported on Windows")
@pytest.mark.subprocess(status=-15, out=lambda s: s.count("flushed") == 1, err=None)
def test_profiler_flushes_on_sigterm() -> None:
    """Profiler must flush the last profile exactly once when the process receives SIGTERM.

    Asserts:
    - upload is called (flush happened).
    - upload is called exactly once (no double-flush from atexit + signal, or scheduler race).

    The process exits with status -15 (killed by SIGTERM) because register_on_exit_signal
    chains onto _raise_default which re-raises SIGTERM with SIG_DFL after all handlers
    complete, so atexit never runs.
    """
    import os
    import signal
    from unittest import mock

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler

    with mock.patch.object(ddup, "upload", lambda *a, **kw: print("flushed", flush=True)):
        p = profiler.Profiler()
        p.start()
        os.kill(os.getpid(), signal.SIGTERM)

        # (unreachable: _raise_default re-raises SIGTERM with SIG_DFL, killing the process)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not supported on Windows")
@pytest.mark.subprocess(out="flushed\napp handler\n", err=None)
def test_restart_registers_again_after_app_replaces_sigterm_handler() -> None:
    """A restart must register the exit signal handler again if the application replaced it."""
    import os
    import signal
    import types
    from typing import Optional
    from unittest import mock

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler

    def application_handler(sig: int, frame: Optional[types.FrameType]) -> None:
        print("app handler", flush=True)
        os._exit(0)

    with mock.patch.object(ddup, "upload", lambda *a, **kw: print("flushed", flush=True)):
        p = profiler.Profiler()
        p.start()
        p.stop(flush=False)

        signal.signal(signal.SIGTERM, application_handler)

        p.start()
        os.kill(os.getpid(), signal.SIGTERM)

        # (unreachable: application_handler exits the process)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery via os.kill not supported on Windows")
@pytest.mark.subprocess(status=-2, out=lambda s: s.count("flushed") == 1, err=None)
def test_profiler_flushes_on_sigint() -> None:
    """Profiler must flush the last profile exactly once on SIGINT when SIGINT is not default_int_handler."""
    import os
    import signal
    from unittest import mock

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    with mock.patch.object(ddup, "upload", lambda *a, **kw: print("flushed", flush=True)):
        p = profiler.Profiler()
        p.start()
        os.kill(os.getpid(), signal.SIGINT)

        # (unreachable: _raise_default re-raises SIGINT with SIG_DFL, killing the process)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery via os.kill not supported on Windows")
@pytest.mark.subprocess(out="flushed\napp handler\n", err=None)
def test_profiler_flushes_on_sigint_before_app_handler() -> None:
    """The profiler SIGINT handler must chain onto an application handler installed before start."""
    import os
    import signal
    import types
    from typing import Optional
    from unittest import mock

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler

    def application_handler(sig: int, frame: Optional[types.FrameType]) -> None:
        print("app handler", flush=True)
        os._exit(0)

    signal.signal(signal.SIGINT, application_handler)

    with mock.patch.object(ddup, "upload", lambda *a, **kw: print("flushed", flush=True)):
        p = profiler.Profiler()
        p.start()
        os.kill(os.getpid(), signal.SIGINT)

        # (unreachable: application_handler exits the process)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery via os.kill not supported on Windows")
@pytest.mark.subprocess(out="flushed\napp handler\n", err=None)
def test_restart_registers_again_after_app_replaces_sigint_handler() -> None:
    """A restart must register the exit signal handler again if the application replaced only SIGINT."""
    import os
    import signal
    import types
    from typing import Optional
    from unittest import mock

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler

    def application_handler(sig: int, frame: Optional[types.FrameType]) -> None:
        print("app handler", flush=True)
        os._exit(0)

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    with mock.patch.object(ddup, "upload", lambda *a, **kw: print("flushed", flush=True)):
        p = profiler.Profiler()
        p.start()
        p.stop(flush=False)

        signal.signal(signal.SIGINT, application_handler)

        p.start()
        os.kill(os.getpid(), signal.SIGINT)

        # (unreachable: application_handler exits the process)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery via os.kill not supported on Windows")
@pytest.mark.subprocess(out="flushed\napp handler\n", err=None)
def test_restart_registers_sigint_after_app_replaces_default_int_handler() -> None:
    """A restart must install the SIGINT handler if the application replaced default_int_handler."""
    import os
    import signal
    import types
    from typing import Optional
    from unittest import mock

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler

    def application_handler(sig: int, frame: Optional[types.FrameType]) -> None:
        print("app handler", flush=True)
        os._exit(0)

    with mock.patch.object(ddup, "upload", lambda *a, **kw: print("flushed", flush=True)):
        p = profiler.Profiler()
        p.start()
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
        p.stop(flush=False)

        signal.signal(signal.SIGINT, application_handler)

        p.start()
        os.kill(os.getpid(), signal.SIGINT)

        # (unreachable: application_handler exits the process)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery via os.kill not supported on Windows")
@pytest.mark.subprocess(status=-2, out=lambda s: s.count("flushed") == 1, err=None)
def test_profiler_keeps_default_int_handler_and_flushes_on_keyboard_interrupt() -> None:
    """With default_int_handler in place, start must leave SIGINT alone and atexit must flush on KeyboardInterrupt."""
    import os
    import signal
    import time
    from unittest import mock

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler

    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler

    # Not a context manager: the patch must still be active when atexit runs.
    mock.patch.object(ddup, "upload", lambda *a, **kw: print("flushed", flush=True)).start()

    p = profiler.Profiler()
    p.start()
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler

    os.kill(os.getpid(), signal.SIGINT)
    # KeyboardInterrupt is raised here; Python runs atexit, then exits via SIGINT.
    time.sleep(10)


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_ENABLED="true"),
    ddtrace_run=True,
    err=None,
)
def test_auto_profiler_blocks_manual_start() -> None:
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
def test_profiler_singleton_after_fork() -> None:
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


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="fork test only on linux")
@pytest.mark.subprocess(timeout=15, err=None)
def test_profiler_child_recovers_ownership_inherited_during_start_rollback():
    import os
    import threading
    from unittest import mock

    from ddtrace.internal import service
    from ddtrace.profiling import profiler

    class TestProfiler(profiler._ProfilerInstance):
        def _start_exporter(self, *args, **kwargs):
            return None

    internal = TestProfiler(
        _memory_collector_enabled=False,
        _stack_collector_enabled=False,
        _lock_collector_enabled=False,
        _pytorch_collector_enabled=False,
        _exception_profiling_enabled=False,
    )
    parent_pid = os.getpid()
    rollback_started = threading.Event()
    finish_rollback = threading.Event()
    failed_scheduler = mock.Mock(status=service.ServiceStatus.STOPPED)
    failed_scheduler.start.side_effect = RuntimeError("start failed")

    def rollback_scheduler():
        if os.getpid() == parent_pid:
            rollback_started.set()
            finish_rollback.wait()
        failed_scheduler.status = service.ServiceStatus.STOPPED

    failed_scheduler._rollback_start.side_effect = rollback_scheduler
    internal._scheduler = failed_scheduler
    wrapped = object.__new__(profiler.Profiler)
    wrapped._profiler = internal

    competing_internal = mock.Mock(status=service.ServiceStatus.STOPPED, _start_cleanup_pending=False)
    competing_internal.start.side_effect = lambda: setattr(competing_internal, "status", service.ServiceStatus.RUNNING)
    competing = object.__new__(profiler.Profiler)
    competing._profiler = competing_internal
    start_error = []

    def fail_start():
        try:
            wrapped.start()
        except RuntimeError as error:
            start_error.append(error)

    with (
        mock.patch("ddtrace.profiling.profiler.uwsgi.check_uwsgi"),
        mock.patch("ddtrace.profiling.profiler.atexit.register"),
        mock.patch("ddtrace.profiling.profiler.atexit.unregister"),
        mock.patch("ddtrace.profiling.profiler.telemetry_writer.product_activated"),
    ):
        starter = threading.Thread(target=fail_start)
        starter.start()
        assert rollback_started.wait(1)

        child_pid = os.fork()
        if child_pid == 0:
            try:
                competing.start()
                child_started = profiler.Profiler._active_instance is competing
            except BaseException:
                child_started = False
            os._exit(0 if child_started else 1)

        finish_rollback.set()
        starter.join(1)
        assert not starter.is_alive()
        assert len(start_error) == 1
        _, status = os.waitpid(child_pid, 0)
        assert os.WIFEXITED(status)
        assert os.WEXITSTATUS(status) == 0


@pytest.mark.skipif(not TESTING_GEVENT, reason="gevent is not available")
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_ENABLED="true"),
    err=lambda stderr: "AssertionError" not in stderr,
)
def test_profiler_atexit_no_assertion_error_with_gevent() -> None:
    """Regression test: atexit callbacks must not raise AssertionError when
    gevent >= 26.4.0 is monkey-patched and the gevent hub is torn down before
    atexit runs (gevent/thread.py _set_greenlet assert glet is not None).
    """
    import ddtrace.auto  # noqa: F401, I001
    import ddtrace.profiling.auto  # noqa: F401

    import gevent.monkey  # noqa: E402

    gevent.monkey.patch_all()

    import time  # noqa: E402

    time.sleep(0.1)

    # We don't need to assert anything here, we only want to make sure the subprocess
    # runs (and exits) without raising an exception/crashing.


def test_unavailable_profiler_matches_real_interface() -> None:
    import inspect

    from ddtrace.profiling import _UnavailableProfiler
    from ddtrace.profiling.profiler import Profiler as RealProfiler

    # __init__ and __getattr__ are part of the callable contract callers rely
    # on (construction and attribute delegation), so include them alongside
    # public methods when comparing interfaces.
    interesting_dunders: set[str] = {"__init__", "__getattr__"}

    def _relevant_methods(cls: type) -> set[str]:
        return {
            name
            for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)
            if not name.startswith("_") or name in interesting_dunders
        }

    real_methods: set[str] = _relevant_methods(RealProfiler)
    stub_methods: set[str] = _relevant_methods(_UnavailableProfiler)
    assert real_methods == stub_methods, (
        f"Interface drift between Profiler and _UnavailableProfiler: "
        f"missing on stub={real_methods - stub_methods}, extra on stub={stub_methods - real_methods}"
    )

    for name in real_methods:
        real_sig: inspect.Signature = inspect.signature(getattr(RealProfiler, name))
        stub_sig: inspect.Signature = inspect.signature(getattr(_UnavailableProfiler, name))
        assert real_sig == stub_sig, f"Signature mismatch on {name}: real={real_sig}, stub={stub_sig}"


def test_unavailable_profiler_preserves_public_name() -> None:
    from ddtrace.profiling import _UnavailableProfiler
    from ddtrace.profiling.profiler import Profiler as RealProfiler

    Profiler: type[_UnavailableProfiler] = type("Profiler", (_UnavailableProfiler,), {})

    assert Profiler.__name__ == RealProfiler.__name__
    assert Profiler.__qualname__ == RealProfiler.__qualname__


def test_unavailable_profiler_raises_import_error() -> None:
    from ddtrace.profiling import _UnavailableProfiler

    _UnavailableProfiler._import_error = RuntimeError("native ext missing")
    try:
        with pytest.raises(ImportError) as excinfo:
            _UnavailableProfiler()
        assert isinstance(excinfo.value.__cause__, RuntimeError)

        # __init__ raises, so drive start/stop/__getattr__ against the class
        # using a bare instance created without calling __init__.
        bare: _UnavailableProfiler = _UnavailableProfiler.__new__(_UnavailableProfiler)
        with pytest.raises(ImportError):
            bare.start()
        with pytest.raises(ImportError):
            bare.stop()
        with pytest.raises(ImportError):
            bare.stop(flush=False)
        with pytest.raises(ImportError):
            _ = bare.status  # delegated attribute on the real Profiler
    finally:
        _UnavailableProfiler._import_error = None
