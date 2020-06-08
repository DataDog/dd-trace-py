import pytest

import ddtrace
from ddtrace.profiling import profiler
from ddtrace.profiling.collector import stack


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
