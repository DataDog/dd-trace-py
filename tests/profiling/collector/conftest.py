import ddtrace
import pytest


@pytest.fixture
def tracer(monkeypatch):
    monkeypatch.setenv("DD_TRACE_STARTUP_LOGS", "0")
    return ddtrace.Tracer()
