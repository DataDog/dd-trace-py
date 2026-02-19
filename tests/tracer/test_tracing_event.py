from dataclasses import InitVar
from dataclasses import dataclass
from types import TracebackType
from typing import Optional

import pytest

from ddtrace._trace.events import TracingEvent
from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import event_field


ExcInfoType = tuple[Optional[type], Optional[BaseException], Optional[TracebackType]]


@pytest.fixture(autouse=True)
def reset_event_hub():
    """Reset event hub after each test to prevent listener leakage between tests."""
    yield
    event_hub.reset()


@dataclass
class TestTracingEvent(TracingEvent):
    event_name = "test.span.event"
    span_type = "custom"
    span_kind = "internal"

    my_span_name: InitVar[str] = event_field()

    def __post_init__(self, my_span_name):
        self.span_name = my_span_name


def test_span_context_event_can_create_and_finish_span(test_spans):
    """Test that a tracing context event starts and finishes a span with expected values."""

    def on_context_started(ctx: core.ExecutionContext):
        event: TracingEvent = ctx.event
        event.tags[COMPONENT] = event.component

        ctx.set_item("span_name", event.span_name)
        ctx.set_item("span_type", event.span_type)
        ctx.set_item("resource", event.resource)
        ctx.set_item("service", event.service)
        ctx.set_item("tags", event.tags)

        _start_span(ctx)

    def on_context_ended(ctx: core.ExecutionContext, err_info: ExcInfoType):
        _finish_span(ctx, err_info)

    core.on(f"context.started.{TestTracingEvent.event_name}", on_context_started)
    core.on(f"context.ended.{TestTracingEvent.event_name}", on_context_ended)

    with core.context_with_event(
        TestTracingEvent(resource="test.resource", service="svc", component="comp", my_span_name="name")
    ):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "name"
    assert span.span_type == "custom"
    assert span._meta[COMPONENT] == "comp"
    assert span.resource == "test.resource"
    assert span.service == "svc"
