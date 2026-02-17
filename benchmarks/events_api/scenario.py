from dataclasses import dataclass

import bm
import bm.utils as utils

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.constants import SPAN_KIND
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import TracingEvent
from ddtrace.internal.core.events import event_field
from ddtrace.trace import tracer


@dataclass
class BenchmarkTracingEvent(TracingEvent):
    event_name = "events.api.event"

    span_name = "benchmarking"
    span_type = "base"
    span_kind = "client"
    component = "test"

    url: str = event_field()
    method: str = event_field()
    status_code: int = event_field()


@dataclass
class BenchmarkDispatchEvent(Event):
    event_name = "events.api.dispatch.event"

    url: str
    method: str
    status_code: int


class SpanContextSubscriber(TracingSubscriber):
    event_name = BenchmarkTracingEvent.event_name

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        span = ctx.span
        event: BenchmarkTracingEvent = ctx.event
        span._set_tag_str("http.url", event.url)
        span._set_tag_str("http.method", event.method)
        span.set_metric("http.status_code", event.status_code)


def _context_started_handler(ctx: core.ExecutionContext) -> None:
    _start_span(ctx, call_trace=True)
    span = ctx.span
    span._set_tag_str("http.url", ctx.get_item("url"))
    span._set_tag_str("http.method", ctx.get_item("method"))
    span.set_metric("http.status_code", ctx.get_item("status_code"))


def _context_ended_handler(ctx: core.ExecutionContext, exc_info) -> None:
    _finish_span(ctx, exc_info)


core.on("context.started.core.api.event", _context_started_handler)
core.on("context.ended.core.api.event", _context_ended_handler)


def _dispatch_listener(*_args) -> None:
    pass


def _dispatch_event_listener(_event: BenchmarkDispatchEvent) -> None:
    pass


core.on("core.api.dispatch.event", _dispatch_listener)
core.on(BenchmarkDispatchEvent.event_name, _dispatch_event_listener)


class EventsAPIScenario(bm.Scenario):
    api: str

    def run(self):
        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        def benchmark_core_api(loops):
            for _ in range(loops):
                with core.context_with_data(
                    "core.api.event",
                    span_name="benchmarking",
                    service="base",
                    resource="test",
                    span_type="base",
                    url="myurl.com",
                    method="GET",
                    status_code=200,
                    tags={COMPONENT: "test", SPAN_KIND: "client"},
                ):
                    pass

        def benchmark_events_api(loops):
            for _ in range(loops):
                with core.context_with_event(
                    BenchmarkTracingEvent(
                        service="base",
                        url="myurl.com",
                        component="test",
                        method="GET",
                        status_code=200,
                        resource="test",
                    )
                ):
                    pass

        def benchmark_dispatch(loops):
            for _ in range(loops):
                core.dispatch("core.api.dispatch.event", ("myurl.com", "GET", 200))

        def benchmark_dispatch_event(loops):
            for _ in range(loops):
                core.dispatch_event(BenchmarkDispatchEvent(url="myurl.com", method="GET", status_code=200))

        if self.api == "core":
            yield benchmark_core_api
        elif self.api == "events":
            yield benchmark_events_api
        elif self.api == "dispatch":
            yield benchmark_dispatch
        elif self.api == "dispatch_event":
            yield benchmark_dispatch_event
        else:
            raise RuntimeError("Unknown benchmark api {!r}".format(self.api))
