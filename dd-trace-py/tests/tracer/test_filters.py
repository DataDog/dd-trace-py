import pytest

from ddtrace.trace import TraceFilter


def test_not_implemented_trace_filter():
    class Filter(TraceFilter):
        pass

    with pytest.raises(TypeError):
        Filter()
