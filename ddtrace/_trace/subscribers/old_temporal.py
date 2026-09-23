import contextvars
from types import TracebackType
from typing import Any

from ddtrace._trace.context import Context
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.temporal import FinishContext
from ddtrace.contrib._events.temporal import OperationNames
from ddtrace.contrib._events.temporal import TemporalActivateWorkflowEvent
from ddtrace.contrib._events.temporal import TemporalEvents
from ddtrace.contrib._events.temporal import TemporalOperationEvent
from ddtrace.contrib._events.temporal import TemporalPropagationEvent
from ddtrace.contrib._events.temporal import TemporalWorkflowLogEvent
from ddtrace.ext import SpanKind
from ddtrace.internal import core
from ddtrace.internal.constants import LOG_ATTR_SPAN_ID
from ddtrace.internal.constants import LOG_ATTR_TRACE_ID
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.fnv import fnv1_64
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


log = get_logger(__name__)
_BAGGAGE_ITEM_SERVICE = "servicename"
_CONTINUE_AS_NEW_TAG = "temporal.continued_as_new"
_TEMPORAL_TAG_PREFIX = "temporal."
_active_workflow_span: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "temporal_active_workflow_span", default=None
)

_SPAN_KINDS = {
    OperationNames.START_ACTIVITY: SpanKind.PRODUCER,
    OperationNames.RUN_ACTIVITY: SpanKind.CONSUMER,
    OperationNames.START_CHILD_WORKFLOW: SpanKind.PRODUCER,
    OperationNames.START_WORKFLOW: SpanKind.PRODUCER,
    OperationNames.SIGNAL_WITH_START_WORKFLOW: SpanKind.PRODUCER,
    OperationNames.RUN_WORKFLOW: SpanKind.CONSUMER,
    OperationNames.SIGNAL_WORKFLOW: SpanKind.PRODUCER,
    OperationNames.SIGNAL_CHILD_WORKFLOW: SpanKind.PRODUCER,
    OperationNames.SIGNAL_EXTERNAL_WORKFLOW: SpanKind.PRODUCER,
    OperationNames.HANDLE_SIGNAL: SpanKind.CONSUMER,
    OperationNames.QUERY_WORKFLOW: SpanKind.PRODUCER,
    OperationNames.HANDLE_QUERY: SpanKind.CONSUMER,
    OperationNames.UPDATE_WORKFLOW: SpanKind.PRODUCER,
    OperationNames.UPDATE_WITH_START_WORKFLOW: SpanKind.PRODUCER,
    OperationNames.VALIDATE_UPDATE: SpanKind.CONSUMER,
    OperationNames.HANDLE_UPDATE: SpanKind.CONSUMER,
    OperationNames.CREATE_SCHEDULE: SpanKind.PRODUCER,
    OperationNames.START_NEXUS_OPERATION: SpanKind.PRODUCER,
    OperationNames.RUN_NEXUS_OPERATION_START_HANDLER: SpanKind.CONSUMER,
    OperationNames.RUN_NEXUS_OPERATION_CANCEL_HANDLER: SpanKind.CONSUMER,
}
_MANUAL_KEEP_OPS = frozenset(
    {
        OperationNames.RUN_WORKFLOW,
        OperationNames.START_WORKFLOW,
        OperationNames.SIGNAL_WITH_START_WORKFLOW,
        OperationNames.SIGNAL_WORKFLOW,
        OperationNames.QUERY_WORKFLOW,
        OperationNames.UPDATE_WORKFLOW,
        OperationNames.UPDATE_WITH_START_WORKFLOW,
        OperationNames.CREATE_SCHEDULE,
        OperationNames.START_ACTIVITY,
    }
)


def _normalize_attribute(key: str) -> str:
    if key.startswith(_TEMPORAL_TAG_PREFIX):
        return key
    if key.lower().startswith("temporal"):
        return _TEMPORAL_TAG_PREFIX + key[len("temporal") :].lstrip(".")
    return _TEMPORAL_TAG_PREFIX + key


def _extract(event: TemporalOperationEvent | TemporalPropagationEvent, carrier: Any) -> Context | None:
    if not carrier:
        return None
    try:
        context = HTTPPropagator.extract(carrier)  # type: ignore[no-untyped-call]
    except Exception:
        if event.allow_invalid_parent_spans:
            return None
        raise
    return context if context.trace_id is not None else None


def _workflow_parent(event: TemporalOperationEvent | TemporalPropagationEvent) -> Context | Any | None:
    context = _extract(event, event.incoming_carrier)
    if context is not None and event.workflow_span_id is not None:
        context.span_id = event.workflow_span_id
    return context or _active_workflow_span.get()


class TemporalOperationSubscriber(TracingSubscriber[TemporalOperationEvent]):
    event_names = (TemporalEvents.OPERATION.value,)

    @classmethod
    def on_before_start(cls, ctx: core.ExecutionContext[TemporalOperationEvent]) -> None:
        event = ctx.event
        if event.use_active_context:
            parent = tracer.context_provider.active()
        else:
            parent = _extract(event, event.incoming_carrier)
            if parent is None and event.workflow_span_id is not None:
                event.incoming_carrier = event.workflow_carrier
                parent = _workflow_parent(event)

        # AIDEV-NOTE: Supply deterministic trace IDs through the parent context;
        # changing span.trace_id after creation breaks the tracer's trace registry.
        if event.deterministic_root_trace and parent is None and event.idempotency_key is not None:
            parent = Context(trace_id=fnv1_64(f"trace:{event.idempotency_key}".encode()), span_id=None, is_remote=True)

        event.distributed_context = parent
        event.use_active_context = False

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[TemporalOperationEvent]) -> None:
        event = ctx.event
        parent = event.distributed_context
        span = span_from_context(ctx)

        for exception_type in event.ignored_exceptions:
            span._ignore_exception(exception_type)

        if event.start_ns is not None:
            span.start_ns = event.start_ns

        if event.idempotency_key is not None:
            span_id = fnv1_64(event.idempotency_key.encode())
            span.span_id = span_id
            span.context.span_id = span_id

        for key, value in event.attributes.items():
            span.set_tag(_normalize_attribute(key), value)

        if event.operation in _MANUAL_KEEP_OPS and (parent is None or event.parent_from_header):
            span.set_tag(MANUAL_KEEP_KEY)

        kind = _SPAN_KINDS.get(event.operation)
        if kind:
            span.set_tag(SPAN_KIND, kind)
            baggage_getter = getattr(parent, "get_baggage_item", None)
            parent_service = baggage_getter(_BAGGAGE_ITEM_SERVICE) if callable(baggage_getter) else None
            if kind == SpanKind.CONSUMER and parent_service and parent_service != event.service:
                span.set_tag("peer.service", parent_service)
        if event.service is not None:
            span.context.set_baggage_item(_BAGGAGE_ITEM_SERVICE, event.service)
        if event.inject:
            HTTPPropagator.inject(span.context, event.outgoing_carrier)
        if event.operation == OperationNames.RUN_WORKFLOW:
            _active_workflow_span.set(span)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[TemporalOperationEvent],
        exc_info: tuple[type | None, BaseException | None, TracebackType | None],
    ) -> None:
        if ctx.get_item("_inner_span") is None:
            return
        event = ctx.event
        span = span_from_context(ctx)
        exception = exc_info[1]

        if event.continued_as_new_exception is not None and isinstance(exception, event.continued_as_new_exception):
            span.set_tag(_CONTINUE_AS_NEW_TAG, "True")
        result = None
        if event.on_span_finish is not None:
            try:
                result = event.on_span_finish(FinishContext(operation=event.operation, exception=exception))
            except Exception:
                log.error("temporal on_span_finish callback for %r raised; ignoring", event.operation, exc_info=True)
        if result is not None and result.extra_tags:
            span.set_tags(dict(result.extra_tags))


class TemporalPropagationSubscriber(Subscriber):
    event_names = (TemporalEvents.PROPAGATE.value,)

    @classmethod
    def on_event(cls, event_instance: TemporalPropagationEvent) -> None:
        context = (
            tracer.context_provider.active() if event_instance.use_active_context else _workflow_parent(event_instance)
        )
        if context is not None:
            HTTPPropagator.inject(context, event_instance.outgoing_carrier)


class TemporalWorkflowLogSubscriber(Subscriber):
    event_names = (TemporalEvents.WORKFLOW_LOG.value,)

    @classmethod
    def on_event(cls, event_instance: TemporalWorkflowLogEvent) -> None:
        span = _active_workflow_span.get()
        if span is not None:
            event_instance.record.__dict__[LOG_ATTR_TRACE_ID] = format_trace_id(span.trace_id)
            event_instance.record.__dict__[LOG_ATTR_SPAN_ID] = str(span.span_id)


class TemporalActivateWorkflowSubscriber(Subscriber):
    event_names = (TemporalEvents.ACTIVATE_WORKFLOW.value,)

    @classmethod
    def on_event(cls, event_instance: TemporalActivateWorkflowEvent) -> None:
        if event_instance.operation_context.get_item("_inner_span") is not None:
            _active_workflow_span.set(span_from_context(event_instance.operation_context))
