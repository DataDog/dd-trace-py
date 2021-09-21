import pytest

import ddtrace
from ddtrace.profiling import Profiler


@pytest.fixture
def tracer(monkeypatch):
    monkeypatch.setenv("DD_TRACE_STARTUP_LOGS", "0")
    return ddtrace.Tracer()


@pytest.fixture
def profiler(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    p = Profiler()
    p.start()
    try:
        yield p
    finally:
        p.stop()
