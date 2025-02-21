import pytest  # noqa: I001


@pytest.mark.subprocess()
def test_lazy_import():
    import ddtrace.auto  # noqa: F401,I001
    from ddtrace.trace import tracer  # noqa: I001

    assert tracer.context_provider.active() is None
    span = tracer.trace("itsatest", service="test", resource="resource", span_type="http")
    assert tracer.context_provider.active() is span

    # Importing asyncio after starting a trace does not remove the current active span
    import asyncio  # noqa: F401

    assert tracer.context_provider.active() is span
    span.finish()
    assert tracer.context_provider.active() is None


@pytest.mark.subprocess()
def test_asyncio_not_imported_by_auto_instrumentation():
    # Module unloading is not supported for asyncio, a simple workaround
    # is to ensure asyncio is not imported by ddtrace.auto or ddtrace-run.
    # If asyncio is imported by ddtrace.auto the asyncio event loop with fail
    # to register new loops in some platforms (e.g. Ubuntuu).
    import sys

    import ddtrace.auto  # noqa: F401

    assert "asyncio" not in sys.modules
