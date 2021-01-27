import os
import subprocess
import sys
import time

import pytest

import ddtrace
from ddtrace.profiling import collector
from ddtrace.profiling import event
from ddtrace.profiling import exporter
from ddtrace.profiling import profiler
from ddtrace.profiling.collector import stack
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
    """Check that the profiler can be stopped twice.

    This is useful since the atexit.unregister call might not exist on Python 2,
    therefore the profiler can be stopped twice (once per the user, once at exit).
    """
    p = profiler.Profiler()
    p.start()
    p.stop(flush=False)
    p.stop(flush=False)


@pytest.mark.parametrize(
    "service_name_var",
    ("DD_SERVICE", "DD_SERVICE_NAME", "DATADOG_SERVICE_NAME"),
)
def test_default_from_env(service_name_var, monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "foobar")
    monkeypatch.setenv(service_name_var, "foobar")
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


def test_env_default(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "foobar")
    monkeypatch.setenv("DD_ENV", "staging")
    monkeypatch.setenv("DD_VERSION", "123")
    prof = profiler.Profiler()
    assert prof.env == "staging"
    assert prof.version == "123"
    assert prof.url is None
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
    assert prof.url is None
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
    assert prof.url is None
    assert prof.tags["foo"] == "bar"
    for exp in prof._profiler._scheduler.exporters:
        if isinstance(exp, http.PprofHTTPExporter):
            assert exp.env == "staging"
            assert exp.version == "123"
            assert exp.tags["foo"] == b"bar"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


@pytest.mark.parametrize(
    "name_var",
    ("DD_API_KEY", "DD_PROFILING_API_KEY"),
)
def test_env_api_key(name_var, monkeypatch):
    monkeypatch.setenv(name_var, "foobar")
    prof = profiler.Profiler()
    _check_url(prof, "https://intake.profile.datadoghq.com", "foobar", endpoint_path="/v1/input")


def test_url():
    prof = profiler.Profiler(url="https://foobar:123")
    _check_url(prof, "https://foobar:123")


def _check_url(prof, url, api_key=None, endpoint_path="/profiling/v1/input"):
    for exp in prof._profiler._scheduler.exporters:
        if isinstance(exp, http.PprofHTTPExporter):
            assert exp.api_key == api_key
            assert exp.endpoint == url
            assert exp.endpoint_path == endpoint_path
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


def test_default_tracer_and_url():
    try:
        ddtrace.tracer.configure(hostname="foobar")
        prof = profiler.Profiler(url="https://foobaz:123")
        _check_url(prof, "https://foobaz:123")
    finally:
        ddtrace.tracer.configure(hostname=ddtrace.Tracer.DEFAULT_HOSTNAME)


def test_tracer_and_url():
    t = ddtrace.Tracer()
    t.configure(hostname="foobar")
    prof = profiler.Profiler(tracer=t, url="https://foobaz:123")
    _check_url(prof, "https://foobaz:123")


def test_tracer_url():
    t = ddtrace.Tracer()
    t.configure(hostname="foobar")
    prof = profiler.Profiler(tracer=t)
    _check_url(prof, "http://foobar:8126")


def test_tracer_url_https():
    t = ddtrace.Tracer()
    t.configure(hostname="foobar", https=True)
    prof = profiler.Profiler(tracer=t)
    _check_url(prof, "https://foobar:8126")


def test_tracer_url_uds():
    t = ddtrace.Tracer()
    t.configure(hostname="foobar", https=True, uds_path="/foobar")
    prof = profiler.Profiler(tracer=t)
    _check_url(prof, "https://foobar:8126")


def test_env_no_api_key():
    prof = profiler.Profiler()
    _check_url(prof, "http://localhost:8126")


def test_env_endpoint_url(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "foobar")
    monkeypatch.setenv("DD_TRACE_AGENT_PORT", "123")
    prof = profiler.Profiler()
    _check_url(prof, "http://foobar:123")


def test_env_endpoint_url_no_agent(monkeypatch):
    monkeypatch.setenv("DD_SITE", "datadoghq.eu")
    monkeypatch.setenv("DD_API_KEY", "123")
    prof = profiler.Profiler()
    _check_url(prof, "https://intake.profile.datadoghq.eu", "123", endpoint_path="/v1/input")


def test_copy():
    p = profiler._ProfilerInstance(env="123", version="dwq", service="foobar")
    c = p.copy()
    assert c == p
    assert p.env == c.env
    assert p.version == c.version
    assert p.service == c.service
    assert p.tracer == c.tracer
    assert p.tags == c.tags


@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
@pytest.mark.skipif(sys.version_info.major == 2, reason="This test does not support Python 2")
def test_gevent_warning(monkeypatch):
    # Set a very short timeout to exit fast
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    subp = subprocess.Popen(
        ("python", os.path.join(os.path.dirname(__file__), "wrong_program_gevent.py")),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    assert subp.wait() == 0
    assert subp.stdout.read() == b""
    assert b"RuntimeWarning: Starting the profiler before using gevent" in subp.stderr.read()


def test_snapshot(monkeypatch):
    class SnapCollect(collector.Collector):
        @staticmethod
        def collect():
            pass

        @staticmethod
        def snapshot():
            return [[event.Event()]]

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
