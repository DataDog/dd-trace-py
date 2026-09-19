from ddtrace import config
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.temporalio import TEMPORAL_CONTEXT_HEADER
from ddtrace.contrib._events.temporalio import TemporalContextForwardEvent
from ddtrace.contrib._events.temporalio import TemporalEvent
from ddtrace.contrib._events.temporalio import TemporalEvents
from ddtrace.contrib._events.temporalio import TemporalHeadersDecodeEvent
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context
from ddtrace.propagation.http import HTTPPropagator


log = get_logger(__name__)


def _inject_context(event: TemporalEvent, ctx: core.ExecutionContext[TemporalEvent]) -> None:
    if not config.temporalio.distributed_tracing:
        return
    span = span_from_context(ctx)
    carrier: dict[str, str] = {}
    try:
        HTTPPropagator.inject(span, carrier)
        if carrier:
            payload = event.payload_converter.to_payloads([carrier])[0]
            event.input_data.headers = {**event.input_data.headers, TEMPORAL_CONTEXT_HEADER: payload}
    except Exception:
        log.debug("Failed to inject trace context into Temporal headers", exc_info=True)


class TemporalTracingSubscriber(TracingSubscriber[TemporalEvent]):
    event_names = (
        TemporalEvents.START_WORKFLOW.value,
        TemporalEvents.SIGNAL_WORKFLOW.value,
        TemporalEvents.QUERY_WORKFLOW.value,
        TemporalEvents.RUN_ACTIVITY.value,
    )

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[TemporalEvent]) -> None:
        event = ctx.event
        if event.event_name != TemporalEvents.RUN_ACTIVITY.value:
            _inject_context(event, ctx)


class TemporalHeadersDecodeSubscriber(Subscriber):
    event_names = (TemporalEvents.DECODE_HEADERS.value,)

    @classmethod
    def on_event(cls, event_instance: TemporalHeadersDecodeEvent) -> None:
        payload = event_instance.input_data.headers.get(TEMPORAL_CONTEXT_HEADER)
        if payload is None:
            return
        try:
            carrier = event_instance.payload_converter.from_payloads([payload])[0]
            if isinstance(carrier, dict):
                event_instance.request_headers = carrier
        except Exception:
            log.debug("Failed to decode trace context from Temporal headers", exc_info=True)


class TemporalContextForwardSubscriber(Subscriber):
    event_names = (TemporalEvents.FORWARD_CONTEXT.value,)

    @classmethod
    def on_event(cls, event_instance: TemporalContextForwardEvent) -> None:
        if not config.temporalio.distributed_tracing:
            return
        payload = event_instance.source_input_data.headers.get(TEMPORAL_CONTEXT_HEADER)
        if payload is not None:
            event_instance.destination_input_data.headers = {
                **event_instance.destination_input_data.headers,
                TEMPORAL_CONTEXT_HEADER: payload,
            }
