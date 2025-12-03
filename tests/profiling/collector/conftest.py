import pytest

import ddtrace


@pytest.fixture
def tracer():
    return ddtrace.trace.tracer
