import pytest

import ddtrace
from ddtrace.trace import Tracer


@pytest.fixture
def tracer() -> Tracer:
    return ddtrace.trace.tracer
