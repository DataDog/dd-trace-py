import pytest

from ddtrace.internal import core


def test_tracing_bundle_registration_preserves_request_completion(tracer, test_spans) -> None:
    from ddtrace._trace import product
    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal.settings._config import Config
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.internal.span_bus import span_from_context

    core.reset_listeners("context.started.web.request")
    core.reset_listeners("context.ended.web.request")
    product.start()
    product.start()
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


@pytest.mark.subprocess(parametrize={"DD_TRACE_ENABLED": ["true", "false"]})
def test_tracing_initialization_supports_manual_instrumentation() -> None:
    import sys

    import ddtrace  # noqa: F401
    from ddtrace.internal import core
    from ddtrace.internal.products import manager
    from ddtrace.internal.settings._config import config
    from ddtrace.trace import tracer

    assert tracer.enabled is config._tracing_enabled
    assert manager._products is None
    assert "ddtrace._trace.product" not in sys.modules
    assert core.has_listeners("context.started.web.request")
    assert core.has_listeners("context.ended.web.request")


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_tracing_product_declaration_does_not_import_subscribers() -> None:
    import sys

    from ddtrace._trace import product
    from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS

    assert product.subscriber_bundles[0].module == "ddtrace._trace.subscribers.web_framework"
    assert "ddtrace._trace.subscribers.web_framework" not in sys.modules
    assert product.subscriber_bundles == (WEB_FRAMEWORK_SUBSCRIBERS,)


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_tracing_product_start_registers_unloaded_subscribers() -> None:
    import sys

    from ddtrace._trace import product
    from ddtrace.internal import core

    assert "ddtrace._trace.subscribers.web_framework" not in sys.modules
    assert not core.has_listeners("context.started.web.request")
    product.start()
    product.start()
    assert core.has_listeners("context.started.web.request")
    assert core.has_listeners("context.ended.web.request")

    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal.settings._config import config
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.trace import tracer

    event = WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(config, "test"),
        request_url="http://localhost/",
        request_method="GET",
        request_headers={},
        headers_case_sensitive=True,
    )
    with core.context_with_event(event) as ctx:
        span = ctx.get_item("_inner_span")
        assert span is not None
        assert span.name == "test.request"
        assert tracer.current_span() is span
    assert span.finished
    assert tracer.current_span() is None


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_subscriber_package_import_does_not_activate_web_bundle() -> None:
    import ddtrace._trace.subscribers  # noqa: F401
    from ddtrace.internal import core

    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")
