import pytest

import ddtrace
from ddtrace.profiling import Profiler
from tests.utils import override_global_config


@pytest.fixture
def tracer():
    with override_global_config(dict(_startup_logs_enabled=False)):
        yield ddtrace.trace.tracer


@pytest.fixture
def profiler(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    p = Profiler()
    p.start()
    try:
        yield p
    finally:
        p.stop()
