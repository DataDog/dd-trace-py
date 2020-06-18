import pytest

import ddtrace
from ddtrace.profiling import profiler
from ddtrace.profiling.collector import stack
from ddtrace.profiling.exporter import http


def test_status():
    p = profiler.Profiler()
    assert repr(p.status) == "STOPPED"
    p.start()
    assert repr(p.status) == "RUNNING"
    p.stop()
    assert repr(p.status) == "STOPPED"


def test_restart():
    p = profiler.Profiler()
    p.start()
    p.stop()
    p.start()
    p.stop()


def test_multiple_stop():
    """Check that the profiler can be stopped twice.

    This is useful since the atexit.unregister call might not exist on Python 2,
    therefore the profiler can be stopped twice (once per the user, once at exit).
    """
    p = profiler.Profiler()
    p.start()
    p.stop()
    p.stop()


@pytest.mark.parametrize(
    "service_name_var", ("DD_SERVICE", "DD_SERVICE_NAME", "DATADOG_SERVICE_NAME"),
)
def test_default_from_env(service_name_var, monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "foobar")
    monkeypatch.setenv(service_name_var, "foobar")
    prof = profiler.Profiler()
    for exporter in prof.exporters:
        if isinstance(exporter, http.PprofHTTPExporter):
            assert exporter.service == "foobar"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


def test_service_api(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "foobar")
    prof = profiler.Profiler(service="foobar")
    assert prof.service == "foobar"
    for exporter in prof.exporters:
        if isinstance(exporter, http.PprofHTTPExporter):
            assert exporter.service == "foobar"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


def test_tracer_api(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "foobar")
    prof = profiler.Profiler(tracer=ddtrace.tracer)
    assert prof.tracer == ddtrace.tracer
    for collector in prof.collectors:
        if isinstance(collector, stack.StackCollector):
            assert collector.tracer == ddtrace.tracer
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
    for exporter in prof.exporters:
        if isinstance(exporter, http.PprofHTTPExporter):
            assert exporter.env == "staging"
            assert exporter.version == "123"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")


def test_env_api(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "foobar")
    prof = profiler.Profiler(env="staging", version="123")
    assert prof.env == "staging"
    assert prof.version == "123"
    for exporter in prof.exporters:
        if isinstance(exporter, http.PprofHTTPExporter):
            assert exporter.env == "staging"
            assert exporter.version == "123"
            break
    else:
        pytest.fail("Unable to find HTTP exporter")
