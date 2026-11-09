from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.internal import core
from ddtrace.internal.core.subscriber import SubscriberBundle


def test_tracing_bundle_registration_preserves_request_completion(tracer, test_spans, monkeypatch) -> None:
    from ddtrace._trace.product import subscriber_bundles
    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal.settings._config import Config
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.internal.span_bus import span_from_context

    monkeypatch.setitem(core.root._data, "tracer", tracer)
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


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "false"})
def test_direct_integration_import_does_not_register_web_subscribers() -> None:
    import sys

    import ddtrace._trace.subscribers.web_framework  # noqa: F401
    import ddtrace.contrib  # noqa: F401
    from ddtrace.internal import core
    from ddtrace.internal.products import manager

    assert manager._products is None
    assert "ddtrace._trace.product" not in sys.modules
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")
    assert not core.has_listeners("web.request.exception")


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "false"})
def test_tracing_product_declaration_does_not_import_subscribers() -> None:
    import sys

    from ddtrace._trace._registration import SUBSCRIBER_BUNDLES
    from ddtrace._trace.product import subscriber_bundles

    assert subscriber_bundles[0].module == "ddtrace._trace.subscribers.web_framework"
    assert "ddtrace._trace.subscribers.web_framework" not in sys.modules
    assert subscriber_bundles == SUBSCRIBER_BUNDLES


@pytest.mark.parametrize("tracing_enabled", [True, False])
def test_tracing_product_registers_bundles_and_unregisters_after_shutdown(tracing_enabled, monkeypatch):
    from ddtrace._trace import product

    bundle = Mock(spec=SubscriberBundle)
    monkeypatch.setattr(product, "subscriber_bundles", (bundle,))
    with patch("ddtrace.trace.tracer") as tracer:
        tracer.enabled = tracing_enabled
        tracer.shutdown.side_effect = lambda: bundle.unregister.assert_not_called()
        product.start()
        bundle.ensure_registered.assert_called_once_with()
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


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "false"})
def test_tracing_subscriber_imports_do_not_activate_listeners() -> None:
    from importlib import import_module

    from ddtrace._trace._registration import SUBSCRIBER_BUNDLES
    from ddtrace.internal import core

    for bundle in SUBSCRIBER_BUNDLES:
        module = import_module(bundle.module)
        for name in bundle.subscribers:
            subscriber = getattr(module, name)
            for event_name in subscriber.event_names:
                assert not core.has_listeners(event_name)
                assert not core.has_listeners(f"context.started.{event_name}")
                assert not core.has_listeners(f"context.ended.{event_name}")


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "false"})
def test_tracing_bundle_declarations_do_not_import_optional_integrations() -> None:
    import sys

    from ddtrace._trace import product

    product.start()
    from ddtrace.internal import core

    for event_name in (
        "span.lifecycle",
        "http.client.request",
        "httpx.request",
        "web.request",
        "messaging.produce",
        "messaging.consume",
        "kafka.produce",
        "kafka.consume",
        "llm.request",
    ):
        assert core.has_listeners(f"context.started.{event_name}")
        assert core.has_listeners(f"context.ended.{event_name}")
    assert core.has_listeners("molten.router.match")
    assert "ddtrace._trace.subscribers.ray" not in sys.modules
    assert "ddtrace._trace.subscribers.aws_durable" in sys.modules


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "false"})
def test_aws_durable_bundle_registers_without_importing_sdk() -> None:
    import sys

    from ddtrace._trace._registration import AWS_DURABLE_SUBSCRIBERS
    from ddtrace.internal import core

    assert not core.has_listeners("context.started.aws.durable.execute")
    AWS_DURABLE_SUBSCRIBERS.register()
    for event_name in ("aws.durable.execute", "aws.durable.invoke", "aws.durable.operation"):
        assert core.has_listeners(f"context.started.{event_name}")
        assert core.has_listeners(f"context.ended.{event_name}")
    assert "aws_durable_execution_sdk_python" not in sys.modules


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "true"})
def test_global_tracer_startup_activates_subscribers_without_loading_integrations():
    import sys

    from ddtrace.internal import core

    for event in ("web.request", "llm.request", "http.client.request", "messaging.produce", "kafka.produce"):
        assert core.has_listeners(f"context.started.{event}")
        assert core.has_listeners(f"context.ended.{event}")
    for module in ("aiohttp", "requests", "ray", "mistralai", "vllm", "aws_durable_execution_sdk_python"):
        assert module not in sys.modules
    assert "ddtrace._trace.subscribers.ray" not in sys.modules

    from ddtrace import config
    from ddtrace.contrib._events.llm import LlmRequestEvent
    from ddtrace.internal.settings.integration import IntegrationConfig

    for component in ("vllm", "mistralai", "google_genai"):
        event = LlmRequestEvent(
            component=component, integration_config=IntegrationConfig(config, component), provider="test"
        )
        with core.context_with_event(event) as ctx:
            span = ctx.get_item("_inner_span")
            assert span is not None
            assert span.name == f"{component}.request"
        assert span.finished


@pytest.mark.subprocess(
    env={"_DD_GLOBAL_TRACER_INIT": "true"},
    parametrize={"STANDALONE": ["true", "false"], "BLOCK_PROCESSOR": ["true", "false"]},
)
def test_tracing_stop_cannot_repopulate_discarded_requests():
    from concurrent.futures import ThreadPoolExecutor
    import os
    from threading import Event
    from unittest.mock import patch

    from ddtrace import config
    from ddtrace._trace import product
    from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
    from ddtrace._trace.subscribers import web_framework
    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal import core
    from ddtrace.internal.settings.asm import config as asm_config
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.internal.settings.standalone import standalone_config
    from ddtrace.trace import tracer

    if os.environ["STANDALONE"] == "true":
        standalone_config.apm_tracing_enabled = False
        asm_config._asm_enabled = True
        tracer.enabled = False
    WEB_FRAMEWORK_SUBSCRIBERS.register()
    event = WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(config, "test"),
        request_url="http://localhost/",
        request_method="GET",
        request_headers={},
        headers_case_sensitive=True,
    )
    entered = Event()
    resume = Event()
    discarded = Event()
    finish_shutdown = Event()
    aggregator = tracer._span_aggregator
    target, name = (
        (aggregator, "on_span_start") if os.environ["BLOCK_PROCESSOR"] == "true" else (web_framework, "_start_span")
    )
    original = getattr(target, name)

    def blocked_start(value):
        entered.set()
        assert resume.wait(10)
        return original(value)

    def blocked_stop(*args, **kwargs):
        discarded.set()
        assert finish_shutdown.wait(10)

    def request():
        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            return ctx

    with patch.object(target, name, blocked_start), patch.object(type(aggregator.writer), "stop", blocked_stop):
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = executor.submit(request)
            stopped = None
            try:
                assert entered.wait(10)
                stopped = executor.submit(product.stop)
                assert discarded.wait(10)
                assert not aggregator._traces
                with core.context_with_event(event) as skipped:
                    assert skipped.get_item("_inner_span") is None
                resume.set()
                ctx = pending.result(timeout=10)
                assert not aggregator._traces
            finally:
                resume.set()
                finish_shutdown.set()
            stopped.result(timeout=10)
    ctx.dispatch_ended_event()
    assert not aggregator._traces
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")
