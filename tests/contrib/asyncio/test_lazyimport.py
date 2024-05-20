import pytest  # noqa: I001


@pytest.mark.subprocess()
def test_lazy_import():
    import ddtrace.auto  # noqa: F401,I001
    from ddtrace import tracer  # noqa: I001

    assert tracer.current_trace_context() is None
    span = tracer.trace("itsatest", service="test", resource="resource", span_type="http")
    assert tracer.current_trace_context() is span

    # Importing asyncio after starting a trace does not remove the current active span
    import asyncio  # noqa: F401

    assert tracer.current_trace_context() is span
    span.finish()
    assert tracer.current_trace_context() is None
