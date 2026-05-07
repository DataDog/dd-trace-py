from types import TracebackType
from typing import Optional

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.aws_durable import AwsDurableEvents
from ddtrace.contrib._events.aws_durable import AwsDurableExecuteEvent
from ddtrace.contrib._events.aws_durable import AwsDurableInvokeEvent
from ddtrace.contrib._events.aws_durable import AwsDurableOperationEvent
from ddtrace.ext import aws_durable
from ddtrace.internal import core


class AwsDurableExecuteSubscriber(TracingSubscriber):
    event_names = (AwsDurableEvents.EXECUTE.value,)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: AwsDurableExecuteEvent = ctx.event
        if event.suspended:
            status = "pending"
        elif exc_info[1] is not None:
            status = "failed"
        else:
            status = "succeeded"
        ctx.span._set_attribute(aws_durable.TAG_INVOCATION_STATUS, status)


class AwsDurableInvokeSubscriber(TracingSubscriber):
    event_names = (AwsDurableEvents.INVOKE.value,)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: AwsDurableInvokeEvent = ctx.event
        if event.replayed is not None:
            ctx.span._set_attribute(aws_durable.TAG_REPLAYED, "true" if event.replayed else "false")
        if event.id is not None:
            ctx.span._set_attribute(aws_durable.TAG_ID, event.id)


class AwsDurableOperationSubscriber(TracingSubscriber):
    event_names = (AwsDurableEvents.OPERATION.value,)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: AwsDurableOperationEvent = ctx.event
        if event.replayed is not None:
            ctx.span._set_attribute(aws_durable.TAG_REPLAYED, "true" if event.replayed else "false")
        if event.id is not None:
            ctx.span._set_attribute(aws_durable.TAG_ID, event.id)
