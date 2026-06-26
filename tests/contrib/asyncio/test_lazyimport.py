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
    # The _asyncio C extension caches asyncio.events.get_event_loop_policy at init. If
    # module cloning unloads asyncio, a later `import asyncio` builds a second module whose
    # Python set_event_loop() no longer agrees with the cached C get_event_loop(), so event
    # loop registration silently breaks (observed with gevent on Linux). asyncio and the
    # _asyncio extension must therefore survive cloning as the same module objects.
    import asyncio  # noqa: F401
    import sys

    import ddtrace
    from ddtrace.bootstrap import cloning

    asyncio_module = sys.modules["asyncio"]
    c_asyncio_module = sys.modules["_asyncio"]

    # Treat asyncio as imported during ddtrace setup (as wrapt does under ddtrace-run) so
    # that cleanup considers it a candidate for unloading regardless of test import order.
    ddtrace.LOADED_MODULES = frozenset(m for m in ddtrace.LOADED_MODULES if m not in ("asyncio", "_asyncio"))

    cloning.enabled = True
    cloning.cleanup_loaded_modules()

    assert sys.modules.get("asyncio") is asyncio_module
    assert sys.modules.get("_asyncio") is c_asyncio_module
