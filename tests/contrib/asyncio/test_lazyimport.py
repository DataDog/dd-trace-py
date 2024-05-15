import pytest
import ddtrace.auto  # noqa: F401,I001
from ddtrace import tracer  # noqa: I001


@pytest.mark.subprocess()
def test_lazy_import():
    assert tracer.current_trace_context() is None
    span = tracer.trace("itsatest", service="test", resource="resource", span_type="http")
    assert tracer.current_trace_context() is not None
    import asyncio  # noqa: EF401

    assert tracer.current_trace_context() is not None
    span.finish()
    assert tracer.current_trace_context() is None
