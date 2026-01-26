from enum import Enum

from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.events import ContextEvent
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


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
        }


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
        }
