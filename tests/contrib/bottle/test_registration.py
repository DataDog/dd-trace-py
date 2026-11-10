import pytest


@pytest.mark.subprocess()
def test_plugin_setup_does_not_reactivate_stopped_tracing() -> None:
    from ddtrace._trace import product
    from ddtrace.contrib.internal.bottle.trace import TracePlugin
    from ddtrace.internal import core
    from ddtrace.internal.products import manager

    # Manual instrumentation has listeners without starting the product manager.
    assert manager._products is None
    assert core.has_listeners("context.started.web.request")
    assert core.has_listeners("context.ended.web.request")
    TracePlugin()

    product.stop()
    TracePlugin()
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")
    assert not core.has_listeners("web.request.exception")
