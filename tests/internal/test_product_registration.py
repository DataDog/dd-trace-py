from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.internal import core
from ddtrace.internal.core.subscriber import SubscriberBundle


def test_tracing_bundle_registration_preserves_request_completion(tracer, test_spans) -> None:
    from ddtrace._trace.product import subscriber_bundles
    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal.settings._config import Config
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.internal.span_bus import span_from_context

    subscriber_bundles[0].register()
    subscriber_bundles[0].register()
    event = WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(Config(), "test"),
        request_url="http://localhost/",
        request_method="GET",
        request_headers={},
        headers_case_sensitive=True,
    )
    with core.context_with_event(event) as ctx:
        span = span_from_context(ctx)
        assert not span.finished
        event.response_status_code = 201
    assert span.finished
    assert span.get_tag("http.status_code") == "201"
    assert test_spans.pop() == [span]


@pytest.mark.subprocess
def test_tracing_subscriber_initialization_supports_manual_instrumentation() -> None:
    import sys

    import ddtrace._trace.subscribers  # noqa: F401
    from ddtrace.internal import core
    from ddtrace.internal.products import manager

    assert manager._products is None
    assert "ddtrace._trace.product" not in sys.modules
    assert core.has_listeners("context.started.web.request")
    assert core.has_listeners("context.ended.web.request")
    assert core.has_listeners("web.request.exception")


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_tracing_product_declaration_does_not_import_subscribers() -> None:
    import sys

    from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
    from ddtrace._trace.product import subscriber_bundles

    assert subscriber_bundles[0].module == "ddtrace._trace.subscribers.web_framework"
    assert "ddtrace._trace.subscribers.web_framework" not in sys.modules
    assert subscriber_bundles == (WEB_FRAMEWORK_SUBSCRIBERS,)


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_subscriber_initialization_after_product_stop_stays_inactive() -> None:
    import sys

    from ddtrace._trace import product
    from ddtrace.internal import core

    assert "ddtrace._trace.subscribers" not in sys.modules
    product.stop()
    assert "ddtrace._trace.subscribers" not in sys.modules

    import ddtrace._trace.subscribers  # noqa: F401

    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")
    assert not core.has_listeners("web.request.exception")


@pytest.mark.parametrize("tracing_enabled", [True, False])
def test_tracing_product_registers_bundles_and_unregisters_after_shutdown(tracing_enabled, monkeypatch):
    from ddtrace._trace import product

    bundle = Mock(spec=SubscriberBundle)
    monkeypatch.setattr(product, "subscriber_bundles", (bundle,))
    with patch("ddtrace.trace.tracer") as tracer:
        tracer.enabled = tracing_enabled
        tracer.shutdown.side_effect = lambda: bundle.unregister.assert_not_called()
        product.start()
        bundle.register.assert_called_once_with()
        product.stop()
        tracer.shutdown.assert_called_once_with()
        bundle.unregister.assert_called_once_with()


def test_tracing_product_retains_listeners_when_shutdown_fails(monkeypatch):
    from ddtrace._trace import product

    bundle = Mock(spec=SubscriberBundle)
    monkeypatch.setattr(product, "subscriber_bundles", (bundle,))
    with patch("ddtrace.trace.tracer") as tracer:
        tracer.enabled = True
        tracer.shutdown.side_effect = RuntimeError("shutdown failed")
        with pytest.raises(RuntimeError, match="shutdown failed"):
            product.stop()
        bundle.unregister.assert_not_called()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_ENABLED="true", DD_APM_TRACING_ENABLED="true"),
    parametrize={
        "DISABLE_BEFORE_STOP": ["true", "false"],
        "STOP_IN_OTHER_TASK": ["true", "false"],
        "DEFER_COMPLETION": ["true", "false"],
    },
    err=lambda err: err.startswith("Shutting down tracer with 1 spans.") and len(err.splitlines()) == 1,
)
def test_tracing_product_stop_discards_active_requests_and_unregisters() -> None:
    import asyncio
    from dataclasses import replace
    import os
    from unittest.mock import patch

    from ddtrace._trace import product
    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal import core
    from ddtrace.internal.products import ProductManager
    from ddtrace.internal.settings._config import Config
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.trace import tracer

    manager = ProductManager()
    manager._products = [("apm-tracing", product)]
    manager.start_products()
    event = WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        supports_spanless_lifecycle=True,
        integration_config=IntegrationConfig(Config(), "test"),
        request_url="http://localhost/",
        request_method="GET",
        request_headers={},
        headers_case_sensitive=True,
    )
    other_task = os.environ["STOP_IN_OTHER_TASK"] == "true"
    deferred = os.environ["DEFER_COMPLETION"] == "true"

    async def stop():
        manager.stop_products()
        assert not tracer.enabled
        assert not tracer._span_aggregator._traces
        assert tracer.context_provider.active() is None
        assert not core.has_listeners("context.started.web.request")
        assert not core.has_listeners("context.ended.web.request")
        assert not core.has_listeners("web.request.exception")

    async def request():
        with core.context_with_event(event, dispatch_end_event=not deferred) as ctx:
            span = ctx.get_item("_inner_span")
            assert span is not None
            assert not span.finished
            assert span.trace_id in tracer._span_aggregator._traces
            if os.environ["DISABLE_BEFORE_STOP"] == "true":
                tracer.enabled = False
            if other_task:
                await asyncio.create_task(stop())
                # Shutdown clears only its caller's ContextVar, not an existing request task's copy.
                assert tracer.current_span() is span
            else:
                await stop()
                assert tracer.current_span() is None

            with core.context_with_event(replace(event)) as new_ctx:
                assert new_ctx.get_item("_inner_span") is None
            event.response_status_code = 201

        if deferred:
            ctx.dispatch_ended_event()
        assert ctx._event is None
        assert not span.finished
        assert span.get_tag("http.status_code") is None
        assert not tracer._span_aggregator._traces
        # A late manual finish cannot resurrect the discarded trace either.
        span.finish()
        assert not tracer._span_aggregator._traces
        assert tracer.context_provider.active() is None

    with patch.object(tracer._span_aggregator.writer, "write") as write:
        asyncio.run(request())
        write.assert_not_called()
