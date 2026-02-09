from types import TracebackType
from typing import Optional
from typing import Tuple

import pytest

from ddtrace._trace.subscribers._base import SpanTracingSubscriber
from ddtrace.constants import SPAN_KIND
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import SpanContextEvent
from ddtrace.internal.core.events import context_event
from ddtrace.internal.core.events import event_field
from ddtrace.trace import tracer


@pytest.fixture(autouse=True)
def reset_event_hub():
    """Reset event hub after each test to prevent listener leakage between tests."""
    yield
    event_hub.reset()


def test_basic_span_context_event(test_spans):
    """Test that SpanContextEvent creates a span with required attributes"""

    @context_event
    class TestSpanEvent(SpanContextEvent):
        event_name = "test.span"
        span_name = "test.operation"
        span_type = "test"
        span_kind = "client"
        component = "test_component"

    class TestSpanSubscriber(SpanTracingSubscriber):
        event_name = TestSpanEvent.event_name

    with core.context_with_event(TestSpanEvent()):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "test.operation"
    assert span.span_type == "test"
    assert span._meta[COMPONENT] == "test_component"
    assert span._meta[SPAN_KIND] == "client"


def test_span_context_event_missing_required_field(test_spans):
    """Test that missing a required attribute raises AttributeError."""

    @context_event
    class TestSpanEvent(SpanContextEvent):
        event_name = "test.span"
        span_name = "test.operation"
        span_kind = "client"
        component = "component"

    class TestSpanSubscriber(SpanTracingSubscriber):
        event_name = TestSpanEvent.event_name

    with pytest.raises(AttributeError):
        with core.context_with_event(TestSpanEvent()):
            pass

    test_spans.assert_span_count(0)


def test_span_context_event_with_service_and_resource(test_spans):
    """Test that service and resource are properly set on the span"""

    @context_event
    class TestSpanEvent(SpanContextEvent):
        event_name = "test.span"
        span_name = "test.operation"
        span_type = "test"
        span_kind = "client"
        component = "test_component"

    class TestSpanSubscriber(SpanTracingSubscriber):
        event_name = TestSpanEvent.event_name

    with core.context_with_event(TestSpanEvent(service="my-service", resource="/api/endpoint")):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.service == "my-service"
    assert span.resource == "/api/endpoint"


def test_span_context_event_post_init(test_spans):
    """Test that __post_init__ can customize component and tags.
    This test also shows than a required attribute can be assigned during post_init
    """

    @context_event
    class TestSpanEvent(SpanContextEvent):
        event_name = "test.span"
        span_name = "test.operation"
        span_type = "test"
        span_kind = "client"

        url: str
        something: str

        def __post_init__(self):
            self.component = self.something
            self.tags["my_url"] = self.url

    class TestSpanSubscriber(SpanTracingSubscriber):
        event_name = TestSpanEvent.event_name

    with core.context_with_event(TestSpanEvent(url="http://", something="foo")):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span._meta[COMPONENT] == "foo"
    assert span._meta["my_url"] == "http://"


def test_span_context_event_with_custom_fields(test_spans):
    """Test that custom fields can be added and accessed in handlers"""

    @context_event
    class TestSpanEvent(SpanContextEvent):
        event_name = "test.span"
        span_name = "test.operation"
        span_type = "test"
        span_kind = "client"
        component = "test_component"

        url: str = event_field(in_context=True)
        status_code: int = event_field(in_context=True)

    class TestSpanSubscriber(SpanTracingSubscriber):
        event_name = TestSpanEvent.event_name

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            span = ctx.span
            span._set_tag_str("http.url", ctx.get_item("url"))

        @classmethod
        def on_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            span = ctx.span
            span.set_metric("http.status_code", ctx.get_item("status_code"))

    with core.context_with_event(TestSpanEvent(url="http://example.com", status_code=200)):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span._meta["http.url"] == "http://example.com"
    assert span._metrics["http.status_code"] == 200


def test_span_context_event_inheritance(test_spans):
    """Test that SpanContextEvent can be inherited and extended"""

    @context_event
    class BaseHTTPEvent(SpanContextEvent):
        event_name = "http.base"
        span_name = "http.request"
        span_type = "http"
        span_kind = "client"
        component = "http"

        url: str = event_field(in_context=True)

    class BaseHTTPSubscriber(SpanTracingSubscriber):
        event_name = BaseHTTPEvent.event_name

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            span = ctx.span
            span._set_tag_str("http.url", ctx.get_item("url"))

    @context_event
    class HTTPClientEvent(BaseHTTPEvent):
        event_name = "http.client"

        method: str = event_field(in_context=True)

    class HTTPClientSubscriber(BaseHTTPSubscriber):
        event_name = HTTPClientEvent.event_name

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            span = ctx.span
            span._set_tag_str("http.method", ctx.get_item("method"))

    with core.context_with_event(HTTPClientEvent(url="http://example.com", method="GET")):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span._meta["http.url"] == "http://example.com"
    assert span._meta["http.method"] == "GET"
    assert span._meta[COMPONENT] == "http"


def test_span_context_event_with_exception(test_spans):
    """Test that exceptions are properly handled and the span is finished"""

    @context_event
    class TestSpanEvent(SpanContextEvent):
        event_name = "test.span"
        span_name = "test.operation"
        span_type = "test"
        span_kind = "client"
        component = "test_component"

    class TestSpanSubscriber(SpanTracingSubscriber):
        event_name = TestSpanEvent.event_name

    with pytest.raises(ValueError):
        with core.context_with_event(TestSpanEvent()):
            raise ValueError("test error")

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.error == 1
    assert span._meta["error.type"] == "builtins.ValueError"
    assert span._meta["error.message"] == "test error"


def test_span_context_event_call_trace_false_with_distributed_context(test_spans):
    """Test that call_trace=False with distributed_context properly set parent"""

    @context_event
    class TestSpanEvent(SpanContextEvent):
        event_name = "test.distributed"
        span_name = "remote.operation"
        span_type = "worker"
        span_kind = "consumer"
        component = "test_component"

    class TestSpanSubscriber(SpanTracingSubscriber):
        event_name = TestSpanEvent.event_name

    with tracer.trace("local.processing"):
        with core.context_with_event(
            TestSpanEvent(call_trace=False, distributed_context=tracer.context_provider.active())
        ):
            # The current active span should still be "local.processing"
            assert tracer.current_span().name == "local.processing"

    test_spans.assert_span_count(2)
    assert test_spans.spans[1].parent_id == test_spans.spans[0].span_id


def test_span_context_event_end_span_false(test_spans):
    """Test that _end_span=False prevents automatic span finishing"""

    @context_event
    class TestSpanEvent(SpanContextEvent):
        event_name = "test.span"
        span_name = "test.operation"
        span_type = "test"
        span_kind = "client"
        component = "test_component"

    class TestSpanSubscriber(SpanTracingSubscriber):
        event_name = TestSpanEvent.event_name
        _end_span = False

    with core.context_with_event(TestSpanEvent()):
        pass

    # Span should be started but not finished
    test_spans.assert_span_count(0)
