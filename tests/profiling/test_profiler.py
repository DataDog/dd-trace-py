import logging
import os
import time

import mock
import pytest

import ddtrace
from ddtrace.profiling import collector
from ddtrace.profiling import event
from ddtrace.profiling import exporter
from ddtrace.profiling import profiler
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import asyncio
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading
from ddtrace.profiling.exporter import http


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


@pytest.mark.subprocess(
    env=dict(DD_API_KEY="foobar", DD_SERVICE="foobar"),
)
def test_default_from_env():
    import pytest

    from ddtrace.profiling import profiler
    from ddtrace.profiling.exporter import http

    prof = profiler.Profiler()
    for exp in prof._profiler._scheduler.exporters:
        if isinstance(exp, http.PprofHTTPExporter):
            assert exp.service == "foobar"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


def test_service_api(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "foobar")
    prof = profiler.Profiler(service="foobar")
    assert prof.service == "foobar"
    for exp in prof._profiler._scheduler.exporters:
        if isinstance(exp, http.PprofHTTPExporter):
            assert exp.service == "foobar"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


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


def test_profiler_init_float_division_regression(run_python_code_in_subprocess):
    """
    Regression test for https://github.com/DataDog/dd-trace-py/pull/3751
      When float division is enabled, the value of `max_events` can be a `float`,
        this is then passed as `deque(maxlen=float)` which is a type error

    File "/var/task/ddtrace/profiling/recorder.py", line 80, in _get_deque_for_event_type
    return collections.deque(maxlen=self.max_events.get(event_type, self.default_max_events))
    TypeError: an integer is required
    """
    code = """
from ddtrace.profiling import profiler
from ddtrace.profiling.collector import stack_event

prof = profiler.Profiler()

# The error only happened for this specific kind of event
# DEV: Yes, this is likely a brittle way to test, but quickest/easiest way to trigger the error
prof._recorder.push_event(stack_event.StackExceptionSampleEvent())
    """

    out, err, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, err
    assert out == b"", err
    assert err == b""


@pytest.mark.subprocess(
    env=dict(DD_API_KEY="foobar", DD_ENV="staging", DD_VERSION="123"),
)
def test_env_default():
    import pytest

    from ddtrace.profiling import profiler
    from ddtrace.profiling.exporter import http

    prof = profiler.Profiler()
    assert prof.env == "staging"
    assert prof.version == "123"
    for exp in prof._profiler._scheduler.exporters:
        if isinstance(exp, http.PprofHTTPExporter):
            assert exp.env == "staging"
            assert exp.version == "123"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


def test_env_api():
    prof = profiler.Profiler(env="staging", version="123")
    assert prof.env == "staging"
    assert prof.version == "123"
    for exp in prof._profiler._scheduler.exporters:
        if isinstance(exp, http.PprofHTTPExporter):
            assert exp.env == "staging"
            assert exp.version == "123"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


def test_tags_api():
    prof = profiler.Profiler(env="staging", version="123", tags={"foo": "bar"})
    assert prof.env == "staging"
    assert prof.version == "123"
    assert prof.tags["foo"] == "bar"
    for exp in prof._profiler._scheduler.exporters:
        if isinstance(exp, http.PprofHTTPExporter):
            assert exp.env == "staging"
            assert exp.version == "123"
            assert exp.tags["foo"] == "bar"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


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


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_AGENTLESS="true", DD_API_KEY="foobar", DD_SITE=None),
    err=None,
)
def test_env_agentless():
    from ddtrace.profiling import profiler
    from tests.profiling.test_profiler import _check_url

    prof = profiler.Profiler()
    _check_url(prof, "https://intake.profile.datadoghq.com", "foobar", endpoint_path="/api/v2/profile")


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_AGENTLESS="true", DD_API_KEY="foobar", DD_SITE="datadoghq.eu"),
    err=None,
)
def test_env_agentless_site():
    from ddtrace.profiling import profiler
    from tests.profiling.test_profiler import _check_url

    prof = profiler.Profiler()
    _check_url(prof, "https://intake.profile.datadoghq.eu", "foobar", endpoint_path="/api/v2/profile")


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_AGENTLESS="false", DD_API_KEY="foobar"),
    err=None,
)
def test_env_no_agentless():
    from ddtrace.profiling import profiler
    from tests.profiling.test_profiler import _check_url

    prof = profiler.Profiler()
    _check_url(prof, "http://localhost:8126", "foobar")


def _check_url(prof, url, api_key, endpoint_path="profiling/v1/input"):
    for exp in prof._profiler._scheduler.exporters:
        if isinstance(exp, http.PprofHTTPExporter):
            assert exp.api_key == api_key
            assert exp.endpoint == url
            assert exp.endpoint_path == endpoint_path
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


def test_env_no_api_key():
    prof = profiler.Profiler()
    _check_url(prof, "http://localhost:8126", os.environ.get("DD_API_KEY"))


@pytest.mark.subprocess(env={"DD_AGENT_HOST": "foobar", "DD_TRACE_AGENT_PORT": "123", "DD_TRACE_AGENT_URL": None})
def test_env_endpoint_url():
    import os

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer as t
    from tests.profiling.test_profiler import _check_url

    prof = profiler.Profiler(tracer=t)
    _check_url(prof, "http://foobar:123", os.environ.get("DD_API_KEY"))


@pytest.mark.subprocess(env=dict(DD_SITE="datadoghq.eu", DD_API_KEY="123"))
def test_env_endpoint_url_no_agent():
    from ddtrace.profiling import profiler
    from tests.profiling.test_profiler import _check_url

    prof = profiler.Profiler()
    _check_url(prof, "http://localhost:8126", "123")


def test_copy():
    p = profiler._ProfilerInstance(env="123", version="dwq", service="foobar")
    c = p.copy()
    assert c == p
    assert p.env == c.env
    assert p.version == c.version
    assert p.service == c.service
    assert p.tracer == c.tracer
    assert p.tags == c.tags


def test_snapshot(monkeypatch):
    class SnapCollect(collector.Collector):
        @staticmethod
        def collect():
            pass

        @staticmethod
        def snapshot():
            return [[event.Event()]]

        def _start_service(self):
            pass

        def _stop_service(self):
            pass

    all_events = {}

    class Exporter(exporter.Exporter):
        def export(self, events, *args, **kwargs):
            all_events["EVENTS"] = events

    class TestProfiler(profiler._ProfilerInstance):
        def _build_default_exporters(self, *args, **kargs):
            return [Exporter()]

    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")
    p = TestProfiler()
    p._collectors = [SnapCollect(p._recorder)]
    p.start()
    time.sleep(2)
    p.stop()
    assert len(all_events["EVENTS"][event.Event]) == 1


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

    class Exporter(exporter.Exporter):
        def export(self, events, *args, **kwargs):
            pass

    class TestProfiler(profiler._ProfilerInstance):
        def _build_default_exporters(self, *args, **kargs):
            return [Exporter()]

    p = TestProfiler()
    err_collector = mock.MagicMock(wraps=ErrCollect(p._recorder))
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
        from ddtrace.profiling import recorder  # noqa:F401
        from ddtrace.profiling import scheduler  # noqa:F401
        from ddtrace.profiling.collector import _lock  # noqa:F401
        from ddtrace.profiling.collector import _task  # noqa:F401
        from ddtrace.profiling.collector import _traceback  # noqa:F401
        from ddtrace.profiling.collector import memalloc  # noqa:F401
        from ddtrace.profiling.collector import stack  # noqa:F401
        from ddtrace.profiling.collector import stack_event  # noqa:F401


@mock.patch("ddtrace.internal.telemetry.telemetry_writer.add_log")
@mock.patch("ddtrace.internal.datadog.profiling.ddup.config")
@mock.patch("ddtrace.internal.datadog.profiling.ddup.failure_msg", "mock failure message")
def test_libdd_failure_telemetry_logging(mock_ddup_config, mock_add_log):
    """Test that libdd initialization failures log to telemetry instead of standard logging"""
    from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL

    test_exception = Exception("Test libdd failure")
    mock_ddup_config.side_effect = test_exception

    prof = profiler._ProfilerInstance()

    mock_add_log.assert_called_once()
    call_args = mock_add_log.call_args

    assert call_args[0][0] == TELEMETRY_LOG_LEVEL.ERROR

    message = call_args[0][1]
    assert "Failed to load libdd" in message
    assert "falling back to legacy mode" in message
    assert "Test libdd failure" in message
    assert "mock failure message" in message
