from dataclasses import InitVar
from dataclasses import dataclass
from types import TracebackType
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import event_field


ExcInfoType = tuple[Optional[type], Optional[BaseException], Optional[TracebackType]]


@dataclass
class TestTracingEvent(TracingEvent):
    event_name = "test.span.event"
    span_type = "custom"
    span_kind = "internal"

    my_span_name: InitVar[str] = event_field()

    def __post_init__(self, my_span_name):
        self.operation_name = my_span_name


def test_tracing_event_can_create_and_finish_span(test_spans):
    """Test that spans with expected values can be started and finished from handlers of
    context_with_event events.
    """

    def on_context_started(ctx: core.ExecutionContext):
        event: TracingEvent = ctx.event
        event.tags[COMPONENT] = event.component

        ctx.set_item("span_name", event.operation_name)
        ctx.set_item("span_type", event.span_type)
        ctx.set_item("resource", event.resource)
        ctx.set_item("service", event.service)
        ctx.set_item("tags", event.tags)

        _start_span(ctx)

    def on_context_ended(ctx: core.ExecutionContext, err_info: ExcInfoType):
        _finish_span(ctx, err_info)

    started_event = f"context.started.{TestTracingEvent.event_name}"
    ended_event = f"context.ended.{TestTracingEvent.event_name}"
    core.on(started_event, on_context_started, name="test_tracing_event_started")
    core.on(ended_event, on_context_ended, name="test_tracing_event_ended")

    try:
        with core.context_with_event(
            TestTracingEvent(
                resource="test.resource", service="svc", component="comp", my_span_name="name", integration_config={}
            )
        ):
            pass
    finally:
        event_hub.reset(started_event, on_context_started)
        event_hub.reset(ended_event, on_context_ended)

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "name"
    assert span.span_type == "custom"
    assert span._get_str_attribute(COMPONENT) == "comp"
    assert span.resource == "test.resource"
    assert span.service == "svc"


def test_tracing_event_create_can_dispatch_without_subclass(test_spans):
    event = TracingEvent.create(
        component="comp",
        integration_config={},
        operation_name="test.create.event",
        span_type="custom",
        span_kind="client",
        service="svc",
        resource="test.resource",
        tags={"my-tag": "my-value"},
    )

    with core.context_with_event(event):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "test.create.event"
    assert span.span_type == "custom"
    assert span.resource == "test.resource"
    assert span.service == "svc"
    assert span._get_str_attribute(COMPONENT) == "comp"
    assert span._get_str_attribute("span.kind") == "client"
    assert span._get_str_attribute("my-tag") == "my-value"
