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
def test_asyncio_not_unloaded_by_module_cloning():
    import asyncio  # noqa: F401
    import sys

    import ddtrace
    from ddtrace.bootstrap import cloning

    asyncio_module = sys.modules["asyncio"]
    c_asyncio_module = sys.modules["_asyncio"]

    # Drop asyncio from the import baseline so cleanup treats it as a candidate for
    # unloading, as it would be when wrapt pulls it in during ddtrace-run setup.
    ddtrace.LOADED_MODULES = frozenset(m for m in ddtrace.LOADED_MODULES if m not in ("asyncio", "_asyncio"))

    cloning.enabled = True
    cloning.cleanup_loaded_modules()

    assert sys.modules.get("asyncio") is asyncio_module
    assert sys.modules.get("_asyncio") is c_asyncio_module
