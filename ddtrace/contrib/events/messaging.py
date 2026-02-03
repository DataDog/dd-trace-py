from enum import Enum

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.events import ContextEvent
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.propagation.http import HTTPPropagator


class MessagingEvents(Enum):
    MESSAGING_PRODUCE = "messaging.produce"
    MESSAGING_CONSUME = "messaging.consume"
    MESSAGING_COMMIT = "messaging.commit"


class MessagingProduceEvent(ContextEvent):
    event_name = MessagingEvents.MESSAGING_PRODUCE.value
    span_kind = SpanKind.PRODUCER

    @classmethod
    def get_default_tags(cls):
        return {
            COMPONENT: cls.component,
            SPAN_KIND: cls.span_kind,
        }

    def __new__(cls, config, operation, provider, tags=None):
        cls.component = config.integration_name

        return {
            "event_name": cls.event_name,
            "span_type": SpanTypes.WORKER,
            "span_name": schematize_messaging_operation(operation, provider, direction=SpanDirection.OUTBOUND),
            "call_trace": True,
            "tags": cls.get_tags(tags),
            "config": config,
        }

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        span = ctx.span
        span.set_metric(_SPAN_MEASURED_KEY, 1)

        config = ctx.get_item("config")
        headers = ctx.get_item("headers")

        if headers is not None and config.distributed_tracing_enabled:
            is_list = isinstance(headers, list)
            if is_list:
                temp_headers = {}
                HTTPPropagator.inject(span.context, temp_headers)
                for key, value in temp_headers.items():
                    headers.append((key, value.encode("utf-8") if isinstance(value, str) else value))
            else:
                HTTPPropagator.inject(span.context, headers)


class MessagingConsumeEvent(ContextEvent):
    event_name = MessagingEvents.MESSAGING_CONSUME.value
    span_kind = SpanKind.CONSUMER

    @classmethod
    def get_default_tags(cls):
        return {COMPONENT: cls.component, SPAN_KIND: cls.span_kind}

    def __new__(cls, config, operation, provider, tags=None):
        cls.component = config.integration_name

        return {
            "event_name": cls.event_name,
            "span_type": SpanTypes.WORKER,
            "call_trace": True,
            "span_name": schematize_messaging_operation(operation, provider, direction=SpanDirection.PROCESSING),
            "tags": cls.get_tags(tags),
            "config": config,
        }

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        span = ctx.span
        span.set_metric(_SPAN_MEASURED_KEY, 1)
