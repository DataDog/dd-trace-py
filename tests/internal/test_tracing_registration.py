import pytest


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_tracing_subscriber_definitions_do_not_register_listeners() -> None:
    from importlib import import_module
    from importlib import reload

    from ddtrace._trace._registration import subscriber_bundles
    from ddtrace.internal import core
    from ddtrace.internal.core.subscriber import ContextSubscriber

    assert {bundle.module.rsplit(".", 1)[-1] for bundle in subscriber_bundles} >= {
        "_base",
        "http_client",
        "messaging",
        "kafka",
        "llm",
        "molten",
        "aws_durable",
        "web_framework",
    }
    modules = {bundle.module: import_module(bundle.module) for bundle in subscriber_bundles}
    for bundle in subscriber_bundles:
        bundle.unregister()
    for module in modules.values():
        reload(module)
    for bundle in subscriber_bundles:
        module = modules[bundle.module]
        for name in bundle.subscribers:
            subscriber = getattr(module, name)
            assert subscriber.auto_register is False
            for event_name in subscriber.event_names:
                prefix = "context.started." if issubclass(subscriber, ContextSubscriber) else ""
                assert not core.has_listeners(prefix + event_name), (name, event_name)


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_product_start_restores_all_tracing_families_without_duplicates() -> None:
    from importlib import import_module

    from ddtrace import config
    from ddtrace._trace import product
    from ddtrace._trace._registration import subscriber_bundles
    from ddtrace._trace.events import TracingEvent
    from ddtrace.internal import core
    from ddtrace.internal.core.subscriber import ContextSubscriber
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.trace import tracer

    product.start()
    for bundle in subscriber_bundles:
        bundle.unregister()
        module = import_module(bundle.module)
        for name in bundle.subscribers:
            subscriber = getattr(module, name)
            for event_name in subscriber.event_names:
                prefix = "context.started." if issubclass(subscriber, ContextSubscriber) else ""
                assert not core.has_listeners(prefix + event_name), (name, event_name)

    product.start()
    product.start()
    for bundle in subscriber_bundles:
        module = import_module(bundle.module)
        for name in bundle.subscribers:
            subscriber = getattr(module, name)
            for event_name in subscriber.event_names:
                prefixes = (
                    ("context.started.", "context.ended.") if issubclass(subscriber, ContextSubscriber) else ("",)
                )
                for prefix in prefixes:
                    assert core.has_listeners(prefix + event_name), (name, event_name)

    with core.context_with_event(
        TracingEvent.create(
            component="registration-test",
            integration_config=IntegrationConfig(config, "registration-test"),
            operation_name="registration.request",
            span_type="custom",
            span_kind="internal",
        )
    ) as ctx:
        span = ctx.get_item("_inner_span")
        assert span is not None
        assert tracer.current_span() is span
    assert span.finished
    assert tracer.current_span() is None


@pytest.mark.subprocess(parametrize={"DD_TRACE_ENABLED": ["true", "false"]})
def test_manual_initialization_activates_all_families_without_optional_sdks() -> None:
    from importlib import import_module
    import sys

    import ddtrace
    from ddtrace._trace._registration import subscriber_bundles
    from ddtrace.internal import core
    from ddtrace.internal.core.subscriber import ContextSubscriber
    from ddtrace.internal.settings._config import config
    from ddtrace.trace import tracer

    assert tracer.enabled is config._tracing_enabled
    assert "ddtrace.contrib" not in sys.modules
    import ddtrace.contrib  # noqa: F401

    for event_name in ("llm.request", "http.client.request", "messaging.produce", "messaging.consume"):
        assert core.has_listeners("context.started." + event_name), event_name
    for bundle in subscriber_bundles:
        module = import_module(bundle.module)
        for name in bundle.subscribers:
            subscriber = getattr(module, name)
            for event_name in subscriber.event_names:
                prefix = "context.started." if issubclass(subscriber, ContextSubscriber) else ""
                assert core.has_listeners(prefix + event_name), (name, event_name)
    assert "ray" not in sys.modules
    assert "ddtrace._trace.subscribers.ray" not in sys.modules
    assert "aws_durable_execution_sdk_python" not in sys.modules
    assert "vllm" not in sys.modules
    assert ddtrace.tracer is tracer


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_deferred_global_initialization_preserves_manual_tracing() -> None:
    import ddtrace.contrib  # noqa: F401
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    for event_name in ("http.client.request", "llm.request", "messaging.produce", "messaging.consume"):
        assert core.has_listeners("context.started." + event_name), event_name
        assert core.has_listeners("context.ended." + event_name), event_name
    # Web and exception activation still require product startup in this deferred mode.
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.exception")
    assert tracer is not None
