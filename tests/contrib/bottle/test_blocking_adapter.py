"""The blocking adapter works without framework-specific objects."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ddtrace.contrib._events.web_framework import WebFrameworkBlockResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestInspectEvent
from ddtrace.contrib._events.web_framework import WebFrameworkResponseInspectEvent
from ddtrace.contrib.internal.web_framework import RequestInspectionDetails
from ddtrace.contrib.internal.web_framework import blocking_execute_adapter
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig


@pytest.fixture
def event():
    return WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(Config(), "test"),
        request_method="GET",
        request_url="http://localhost/hello/world",
        request_headers={"x-datadog-trace-id": "123", "x-datadog-parent-id": "456"},
        request_route="/hello/<name>",
        resource="GET /hello/<name>",
        headers_case_sensitive=True,
        activate_distributed_headers=True,
        distributed_headers_config_override=True,
    )


class FakeResponse:
    """Minimal stand-in for a framework response type, unrelated to Bottle."""

    def __init__(self, body, status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}


@pytest.mark.parametrize("block_at", ["start", "response"])
def test_blocking_adapter_serves_responses_for_any_framework(event, tracer, test_spans, block_at):
    """The shared blocking protocol is driven entirely by the adapter's callbacks."""
    closed = []
    inspected = []
    block = SimpleNamespace(status_code=418, type="none", location="")
    discarded = SimpleNamespace(close=lambda: closed.append("closed"))
    execute = blocking_execute_adapter(
        request_details=lambda kwargs: RequestInspectionDetails(
            peer_ip="10.0.0.1", cookies={"session": "abc"}, path_params=kwargs
        ),
        is_response_object=lambda outcome: isinstance(outcome, FakeResponse),
        extract_body=lambda outcome: outcome.body,
        build_response=lambda status, headers, body: FakeResponse(body, status, dict(headers)),
    )

    class Observer(Subscriber):
        event_names = (
            WebFrameworkRequestInspectEvent.event_name,
            WebFrameworkResponseInspectEvent.event_name,
            WebFrameworkBlockResponseEvent.event_name,
        )

        @classmethod
        def on_event(cls, notification):
            if isinstance(notification, WebFrameworkBlockResponseEvent):
                assert notification.block is block
                notification.response = (418, {"Content-Type": "text/html"}, b"teapot")
            elif isinstance(notification, WebFrameworkRequestInspectEvent):
                inspected.append((notification.peer_ip, dict(notification.cookies), dict(notification.path_params)))
                if block_at == "start":
                    raise BlockingException(block)
            elif block_at == "response":
                raise BlockingException(block)

    callback = Mock(return_value=FakeResponse(discarded))

    def complete(request_event, result, error):
        request_event.response_status_code = getattr(result, "status", 200)

    try:

        def invoke():
            result = callback(name="world")
            complete(event, result, None)
            return result

        with core.context_with_event(event):
            result = execute(event, invoke, {"name": "world"}, complete)
    finally:
        Observer.unregister()

    assert isinstance(result, FakeResponse)
    assert (result.status, result.body) == (418, b"teapot")
    assert result.headers == {"Content-Type": "text/html"}
    assert inspected == [("10.0.0.1", {"session": "abc"}, {"name": "world"})]
    # Only a block after the callback ran has an outcome to discard.
    assert closed == (["closed"] if block_at == "response" else [])
    assert callback.call_count == int(block_at == "response")
