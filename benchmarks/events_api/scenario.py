from dataclasses import dataclass

import bm
import bm.utils as utils

from ddtrace._trace.events import TracingEvent
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.trace_handlers import _finish_span
from ddtrace._trace.trace_handlers import _start_span
from ddtrace.constants import SPAN_KIND
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.trace import tracer


@dataclass
class BenchmarkTracingEvent(TracingEvent):
    event_name = "events.api.event"

    operation_name = "benchmarking"
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


class EventsAPIScenario(bm.Scenario):
    api: str

    def run(self):
        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        def benchmark_core_api(loops):
            # Register benchmark specific handlers
            def _context_started_handler(ctx: core.ExecutionContext) -> None:
                _start_span(ctx, call_trace=True)
                span = ctx.span
                span._set_attribute("http.url", ctx.get_item("url"))
                span._set_attribute("http.method", ctx.get_item("method"))
                span._set_attribute("http.status_code", ctx.get_item("status_code"))

            def _context_ended_handler(ctx: core.ExecutionContext, exc_info) -> None:
                _finish_span(ctx, exc_info)

            core.on("context.started.core.api.event", _context_started_handler)
            core.on("context.ended.core.api.event", _context_ended_handler)

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
            # Register benchmark specific subscriber
            class SpanContextSubscriber(TracingSubscriber):
                event_names = (BenchmarkTracingEvent.event_name,)

                @classmethod
                def on_started(cls, ctx: core.ExecutionContext) -> None:
                    span = ctx.span
                    event: BenchmarkTracingEvent = ctx.event
                    span._set_attribute("http.url", event.url)
                    span._set_attribute("http.method", event.method)
                    span._set_attribute("http.status_code", event.status_code)

            for _ in range(loops):
                with core.context_with_event(
                    BenchmarkTracingEvent(
                        service="base",
                        url="myurl.com",
                        component="test",
                        integration_config={},
                        method="GET",
                        status_code=200,
                        resource="test",
                    )
                ):
                    pass

        def benchmark_dispatch(loops):
            # Register benchmark specific listener
            def _dispatch_listener(*_args) -> None:
                pass

            core.on("core.api.dispatch.event", _dispatch_listener)

            for _ in range(loops):
                core.dispatch("core.api.dispatch.event", ("myurl.com", "GET", 200))

        def benchmark_dispatch_event(loops):
            class DispatchEventSubscriber(Subscriber):
                event_names = (BenchmarkDispatchEvent.event_name,)

                @classmethod
                def on_event(cls, event_instance: BenchmarkDispatchEvent) -> None:
                    pass

            for _ in range(loops):
                core.dispatch_event(BenchmarkDispatchEvent(url="myurl.com", method="GET", status_code=200))

        def benchmark_trace_api(loops):
            for _ in range(loops):
                span = tracer.trace(
                    "benchmarking",
                    service="base",
                    resource="test",
                    span_type="base",
                )
                span._set_attribute(COMPONENT, "test")
                span._set_attribute(SPAN_KIND, "client")
                span._set_attribute("http.url", "myurl.com")
                span._set_attribute("http.method", "GET")
                span._set_attribute("http.status_code", 200)
                span.finish()

        if self.api == "core":
            yield benchmark_core_api
        elif self.api == "events":
            yield benchmark_events_api
        elif self.api == "dispatch":
            yield benchmark_dispatch
        elif self.api == "dispatch_event":
            yield benchmark_dispatch_event
        elif self.api == "trace_api":
            yield benchmark_trace_api
        else:
            raise RuntimeError("Unknown benchmark api {!r}".format(self.api))
