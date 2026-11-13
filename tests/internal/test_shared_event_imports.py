from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "original_module, canonical_module, names",
    [
        (
            "ddtrace.contrib._events.http",
            "ddtrace.internal.core.http",
            (
                "HttpBaseEvent",
                "HttpRequestBaseEvent",
                "JsonType",
                "_HttpBaseResponse",
                "_HttpResponse",
                "_HttpResponseWithReason",
                "_HttpResponseWithStatus",
                "_HttpResponseWithStatusCode",
            ),
        ),
        (
            "ddtrace.contrib._events.span_lifecycle",
            "ddtrace.internal.core.span_lifecycle",
            ("SpanLifecycleEvents", "SpanLifecycleListener"),
        ),
        (
            "ddtrace.contrib._events.web_framework",
            "ddtrace._trace.web_framework",
            ("WebFrameworkEvents", "WebFrameworkRequestEvent"),
        ),
    ],
)
def test_existing_event_imports_preserve_type_identity(original_module, canonical_module, names):
    original = import_module(original_module)
    canonical = import_module(canonical_module)

    for name in names:
        assert getattr(original, name) is getattr(canonical, name)


@pytest.mark.subprocess(parametrize={"DD_TRACE_ENABLED": ["true", "false"]})
def test_bare_import_does_not_load_contrib() -> None:
    import sys

    import ddtrace  # noqa: F401

    assert "ddtrace.contrib" not in sys.modules
    assert "ddtrace._trace.subscribers" not in sys.modules
    assert "ddtrace._trace.http_meta" not in sys.modules


@pytest.mark.subprocess(
    env={"_DD_GLOBAL_TRACER_INIT": "false"},
    parametrize={"SUBSCRIBER_ENTRYPOINT": ["ddtrace.contrib", "ddtrace._trace.subscribers"]},
)
def test_existing_imports_still_activate_web_subscribers() -> None:
    from importlib import import_module
    import os

    import_module(os.environ["SUBSCRIBER_ENTRYPOINT"])

    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal import core
    from ddtrace.internal.settings._config import config
    from ddtrace.internal.settings.integration import IntegrationConfig

    assert core.has_listeners("context.started.web.request")
    assert core.has_listeners("context.ended.web.request")
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
        assert not span.finished
        event.response_status_code = 201
    assert span.finished
    assert span.get_tag("http.status_code") == "201"
