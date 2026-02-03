from enum import Enum

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib.events.messaging import MessagingConsumeEvent
from ddtrace.contrib.events.messaging import MessagingProduceEvent
from ddtrace.ext import kombu as kombux
from ddtrace.internal import core


class KombuMessagingEvents(Enum):
    KOMBU_MESSAGING_PUBLISH = "kombu.messaging.publish"
    KOMBU_MESSAGING_RECEIVE = "kombu.messaging.receive"


class KombuMessagingPublishEvent(MessagingProduceEvent):
    event_name = KombuMessagingEvents.KOMBU_MESSAGING_PUBLISH.value

    def __new__(
        cls,
        config,
        operation,
        provider,
        exchange,
        routing_key,
        body_length,
        connection_tags,
        headers,
        tags=None,
    ):
        event_data = super().__new__(cls, config, operation, provider, tags)

        instance_tags = {
            kombux.EXCHANGE: exchange,
            kombux.ROUTING_KEY: routing_key,
            **connection_tags,
        }

        event_data["resource"] = exchange
        event_data["headers"] = headers
        event_data["body_length"] = body_length
        event_data["tags"].update(instance_tags)
        return event_data

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        span = ctx.span
        span.set_metric(kombux.BODY_LEN, ctx.get_item("body_length"))

        resource = ctx.get_item("resource")
        if resource:
            span.resource = resource


class KombuMessagingReceiveEvent(MessagingConsumeEvent):
    event_name = KombuMessagingEvents.KOMBU_MESSAGING_RECEIVE.value

    def __new__(
        cls,
        config,
        operation,
        provider,
        exchange,
        routing_key,
        connection_tags,
        tags=None,
    ):
        event_data = super().__new__(cls, config, operation, provider, tags)

        instance_tags = {
            kombux.EXCHANGE: exchange,
            kombux.ROUTING_KEY: routing_key,
            **connection_tags,
        }

        event_data["resource"] = exchange
        event_data["tags"].update(instance_tags)
        return event_data

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        span = ctx.span
        span.set_metric(_SPAN_MEASURED_KEY, 1)

        # Set resource from event data
        resource = ctx.get_item("resource")
        if resource:
            span.resource = resource
