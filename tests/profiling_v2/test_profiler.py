import logging
import time

import mock
import pytest

import ddtrace
from ddtrace.profiling import collector
from ddtrace.profiling import profiler
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import asyncio
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading


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
            return []

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
    p.stop(flush=False)


def test_profiler_serverless(monkeypatch):
    # type: (...) -> None
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "foobar")
    p = profiler.Profiler()
    assert isinstance(p._scheduler, scheduler.ServerlessScheduler)
    assert p.tags["functionname"] == "foobar"


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
        from ddtrace.profiling import event  # noqa:F401
        from ddtrace.profiling import profiler  # noqa:F401
        from ddtrace.profiling import scheduler  # noqa:F401
        from ddtrace.profiling.collector import _lock  # noqa:F401
        from ddtrace.profiling.collector import _task  # noqa:F401
        from ddtrace.profiling.collector import _traceback  # noqa:F401
        from ddtrace.profiling.collector import memalloc  # noqa:F401
        from ddtrace.profiling.collector import stack  # noqa:F401


@mock.patch("ddtrace.internal.telemetry.telemetry_writer.add_log")
@mock.patch("ddtrace.internal.datadog.profiling.ddup.config")
@mock.patch("ddtrace.internal.datadog.profiling.ddup.failure_msg", "mock failure message")
def test_libdd_failure_telemetry_logging(mock_ddup_config, mock_add_log):
    """Test that libdd initialization failures log to telemetry instead of standard logging"""
    from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL

    test_exception = Exception("Test libdd failure")
    mock_ddup_config.side_effect = test_exception

    profiler._ProfilerInstance(_stack_v2_enabled=False)

    mock_add_log.assert_called_once()
    call_args = mock_add_log.call_args

    assert call_args[0][0] == TELEMETRY_LOG_LEVEL.ERROR

    message = call_args[0][1]
    assert "Failed to load libdd" in message
    assert "falling back to legacy mode" in message
    assert "Test libdd failure" in message
    assert "mock failure message" in message


@mock.patch("ddtrace.internal.telemetry.telemetry_writer.add_log")
@mock.patch("ddtrace.internal.datadog.profiling.ddup.config")
@mock.patch("ddtrace.internal.datadog.profiling.ddup.failure_msg", "mock failure message")
@mock.patch("ddtrace.settings.profiling.config.stack.v2_enabled", True)
def test_libdd_failure_stack_v2_telemetry_logging(mock_ddup_config, mock_add_log):
    """Test that libdd initialization failures with stack v2 enabled log both failures to telemetry"""
    from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL

    test_exception = Exception("Test libdd failure")
    mock_ddup_config.side_effect = test_exception

    profiler._ProfilerInstance(_stack_v2_enabled=True)

    assert mock_add_log.call_count == 2

    first_call = mock_add_log.call_args_list[0]
    assert first_call[0][0] == TELEMETRY_LOG_LEVEL.ERROR
    libdd_message = first_call[0][1]
    assert "Failed to load libdd" in libdd_message
    assert "falling back to legacy mode" in libdd_message
    assert "Test libdd failure" in libdd_message
    assert "mock failure message" in libdd_message

    second_call = mock_add_log.call_args_list[1]
    assert second_call[0][0] == TELEMETRY_LOG_LEVEL.ERROR
    stack_v2_message = second_call[0][1]
    assert "Disabling stack_v2 as libdd collector failed to initialize" in stack_v2_message


@mock.patch("ddtrace.internal.telemetry.telemetry_writer.add_log")
@mock.patch("ddtrace.internal.datadog.profiling.ddup.config")
@mock.patch("ddtrace.internal.datadog.profiling.ddup.failure_msg", "mock failure message")
@mock.patch("ddtrace.settings.profiling.config._injected", True)
def test_libdd_failure_injected_telemetry_logging(mock_ddup_config, mock_add_log):
    """Test that libdd initialization failures in injected environments log both failures to telemetry"""
    from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL

    test_exception = Exception("Test libdd failure")
    mock_ddup_config.side_effect = test_exception

    profiler._ProfilerInstance(_stack_v2_enabled=False)

    assert mock_add_log.call_count == 2

    first_call = mock_add_log.call_args_list[0]
    assert first_call[0][0] == TELEMETRY_LOG_LEVEL.ERROR
    libdd_message = first_call[0][1]
    assert "Failed to load libdd" in libdd_message
    assert "falling back to legacy mode" in libdd_message
    assert "Test libdd failure" in libdd_message
    assert "mock failure message" in libdd_message

    second_call = mock_add_log.call_args_list[1]
    assert second_call[0][0] == TELEMETRY_LOG_LEVEL.ERROR
    injection_message = second_call[0][1]
    assert "Profiling failures occurred in an injected instance of ddtrace, disabling profiling" in injection_message
