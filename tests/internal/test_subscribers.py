from dataclasses import InitVar
from dataclasses import dataclass

import pytest

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.constants import SPAN_KIND
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import TracingEvent
from ddtrace.internal.core.events import event_field
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.trace import tracer


called = []


@pytest.fixture(autouse=True)
def reset_event_hub():
    """Reset event hub after each test to prevent listener leakage between tests."""
    yield
    event_hub.reset()
    called.clear()


@dataclass
class TestEvent(Event):
    event_name = "test.subscriber.event"


def test_base_subscriber():
    """Test that a direct BaseSubscriber receives dispatched events."""

    class DirectSubscriber(Subscriber):
        event_names = (TestEvent.event_name,)

        @classmethod
        def on_event(cls, event_instance):
            called.append(event_instance.event_name)

    core.dispatch_event(TestEvent())

    assert called == [TestEvent.event_name]


def test_base_subscriber_inheritance():
    """Test that parent and child BaseSubscriber handlers run in order."""

    class ParentSubscriber(Subscriber):
        @classmethod
        def on_event(cls, event_instance):
            called.append("parent")

    class ChildSubscriber(ParentSubscriber):
        event_names = (TestEvent.event_name,)

        @classmethod
        def on_event(cls, event_instance):
            called.append("child")

    core.dispatch_event(TestEvent())

    assert called == ["parent", "child"]


def test_base_subscriber_multiple_event_names():
    """Test that a Subscriber can register for and handle multiple events."""

    @dataclass
    class TestEventOne(Event):
        event_name = "test.subscriber.event.one"

    @dataclass
    class TestEventTwo(Event):
        event_name = "test.subscriber.event.two"

    class MultiEventSubscriber(Subscriber):
        event_names = (TestEventOne.event_name, TestEventTwo.event_name)

        @classmethod
        def on_event(cls, event_instance):
            called.append(event_instance.event_name)

    core.dispatch_event(TestEventOne())
    core.dispatch_event(TestEventTwo())

    assert called == [TestEventOne.event_name, TestEventTwo.event_name]


def test_base_context_subscriber():
    """Test that context start/end callbacks run for a direct BaseContextSubscriber."""

    @dataclass
    class TestContextEventWithAttributes(Event):
        event_name = "test.subscriber.context.attribute"

        in_context: str = event_field()
        not_in_context: InitVar[str] = event_field()

    class DirectSubscriber(ContextSubscriber):
        event_names = (TestContextEventWithAttributes.event_name,)

        @classmethod
        def on_started(cls, ctx):
            called.append("started")

            event: TestContextEventWithAttributes = ctx.event
            called.append(event.in_context)
            assert getattr(event, "not_in_context", None) is None

        @classmethod
        def on_ended(cls, ctx, exc_info):
            called.append("ended")

    with core.context_with_event(TestContextEventWithAttributes(in_context="foo", not_in_context="bar")):
        pass

    assert called == ["started", "foo", "ended"]


def test_base_context_subscriber_inheritance():
    """Test that inherited BaseContextSubscriber callbacks run parent then child."""

    @dataclass
    class TestContextEvent(Event):
        event_name = "test.subscriber.context"

    class ParentSubscriber(ContextSubscriber):
        @classmethod
        def on_started(cls, ctx):
            called.append("parent_started")

        @classmethod
        def on_ended(cls, ctx, exc_info):
            called.append("parent_ended")

    class ChildSubscriber(ParentSubscriber):
        event_names = (TestContextEvent.event_name,)

        @classmethod
        def on_started(cls, ctx):
            called.append("child_started")

        @classmethod
        def on_ended(cls, ctx, exc_info):
            called.append("child_ended")

    with core.context_with_event(TestContextEvent()):
        pass

    assert called == ["parent_started", "child_started", "parent_ended", "child_ended"]


def test_base_tracing_subscriber(test_spans):
    """Test that TracingSubscriber creates a span with expected core attributes."""

    @dataclass
    class TestTracingEvent(TracingEvent):
        event_name = "test.subscriber.tracing"
        span_name = "test.subscriber.span"
        span_type = "custom"
        span_kind = "internal"

    class TestTracingSubscriber(TracingSubscriber):
        event_names = (TestTracingEvent.event_name,)

    with core.context_with_event(
        TestTracingEvent(component="test-component", service="my-service", resource="/api/endpoint")
    ):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.service == "my-service"
    assert span.span_type == "custom"
    assert span.name == "test.subscriber.span"
    assert span._meta[COMPONENT] == "test-component"
    assert span._meta[SPAN_KIND] == "internal"
    assert span.resource == "/api/endpoint"


def test_span_context_event_missing_required_field(test_spans):
    """Test that missing required tracing attributes raises an AttributeError."""

    @dataclass
    class TestTracingEvent(TracingEvent):
        event_name = "test.span"
        span_name = "test.operation"
        # Missing service_type
        span_kind = "client"

    class TestTracingSubscriber(TracingSubscriber):
        event_names = (TestTracingEvent.event_name,)

    with pytest.raises(AttributeError):
        with core.context_with_event(TestTracingEvent(component="component")):
            pass

    test_spans.assert_span_count(0)


def test_span_context_event_with_custom_fields(test_spans):
    """Test that custom fields can be added and accessed in handlers"""

    @dataclass
    class TestTracingEvent(TracingEvent):
        event_name = "test.span"
        span_type = "test"
        span_kind = "client"

        my_op: InitVar[str] = event_field()
        my_arg: InitVar[str] = event_field()
        status_code: int = event_field()

        def __post_init__(self, my_op, my_arg):
            self.span_name = f"{my_op}.{my_arg}"

    class TestSpanSubscriber(TracingSubscriber):
        event_names = (TestTracingEvent.event_name,)

        @classmethod
        def on_ended(cls, ctx: core.ExecutionContext, exc_info) -> None:
            span = ctx.span
            span.set_metric("http.status_code", ctx.event.status_code)

    with core.context_with_event(TestTracingEvent(my_op="op", my_arg="arg", component="comp", status_code=200)):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "op.arg"
    assert span._metrics["http.status_code"] == 200


def test_span_context_event_inheritance(test_spans):
    """Test that tracing events and subscribers can be inherited and extended."""

    @dataclass
    class BaseHTTPEvent(TracingEvent):
        event_name = "http.base"
        span_name = "http.request"
        span_type = "http"
        span_kind = "client"

        url: str = event_field()

    class BaseHTTPSubscriber(TracingSubscriber):
        event_names = (BaseHTTPEvent.event_name,)

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            span = ctx.span
            span._set_tag_str("http.url", ctx.event.url)

    @dataclass
    class HTTPClientEvent(BaseHTTPEvent):
        event_name = "http.client"

        method: str = event_field()

    class HTTPClientSubscriber(BaseHTTPSubscriber):
        event_names = (HTTPClientEvent.event_name,)

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            span = ctx.span
            span._set_tag_str("http.method", ctx.event.method)

    with core.context_with_event(HTTPClientEvent(url="http://example.com", component="http", method="GET")):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span._meta["http.url"] == "http://example.com"
    assert span._meta["http.method"] == "GET"
    assert span._meta[COMPONENT] == "http"


def test_span_context_event_with_exception(test_spans):
    """Test that raised exceptions are recorded on the created span."""

    @dataclass
    class TestSpanEvent(TracingEvent):
        event_name = "test.span"
        span_name = "test.operation"
        span_type = "test"
        span_kind = "client"

    class TestSpanSubscriber(TracingSubscriber):
        event_names = (TestSpanEvent.event_name,)

    with pytest.raises(ValueError):
        with core.context_with_event(TestSpanEvent(component="test_component")):
            raise ValueError("test error")

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.error == 1
    assert span._meta["error.type"] == "builtins.ValueError"
    assert span._meta["error.message"] == "test error"


def test_span_context_event_no_active_context_with_distributed_context(test_spans):
    """Test that use_active_context=False keeps active span and links parent from distributed context."""

    @dataclass
    class TestSpanEvent(TracingEvent):
        event_name = "test.distributed"
        span_name = "remote.operation"
        span_type = "worker"
        span_kind = "consumer"

    class TestSpanSubscriber(TracingSubscriber):
        event_names = (TestSpanEvent.event_name,)

    with tracer.trace("local.processing"):
        with core.context_with_event(
            TestSpanEvent(
                component="test_component",
                use_active_context=False,
                activate=False,
                distributed_context=tracer.context_provider.active(),
            )
        ):
            # The current active span should still be "local.processing"
            current_span = tracer.current_span()
            assert current_span is not None
            assert current_span.name == "local.processing"

    test_spans.assert_span_count(2)
    assert test_spans.spans[1].parent_id == test_spans.spans[0].span_id


def test_span_context_event_end_span_false(test_spans):
    """Test that setting _end_span=False prevents automatic span finishing."""

    @dataclass
    class TestSpanEvent(TracingEvent):
        event_name = "test.span"
        span_name = "test.operation"
        span_type = "test"
        span_kind = "client"

    class TestSpanSubscriber(TracingSubscriber):
        event_names = (TestSpanEvent.event_name,)
        _end_span = False

    with core.context_with_event(TestSpanEvent(component="test_component")):
        pass

    # Span should be started but not finished
    test_spans.assert_span_count(0)
