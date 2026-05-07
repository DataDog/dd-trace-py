from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import aws_durable
from ddtrace.internal.core.events import event_field


class AwsDurableEvents(Enum):
    EXECUTE = "aws.durable.execute"
    INVOKE = "aws.durable.invoke"
    OPERATION = "aws.durable.operation"


@dataclass
class AwsDurableExecuteEvent(TracingEvent):
    event_name = AwsDurableEvents.EXECUTE.value

    span_kind = SpanKind.INTERNAL
    span_type = SpanTypes.SERVERLESS

    execution_arn: Optional[str] = event_field(default=None)
    is_replay_execution: Optional[bool] = event_field(default=None)
    suspended: bool = event_field(default=False)

    def __post_init__(self) -> None:
        self.operation_name = self.event_name
        if self.execution_arn:
            self.tags[aws_durable.TAG_EXECUTION_ARN] = self.execution_arn
        if self.is_replay_execution is not None:
            self.tags[aws_durable.TAG_REPLAYED] = "true" if self.is_replay_execution else "false"


@dataclass
class AwsDurableInvokeEvent(TracingEvent):
    event_name = AwsDurableEvents.INVOKE.value

    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.SERVERLESS

    operation: str = event_field()
    invoke_function_name: str = event_field()
    replayed: Optional[bool] = event_field(default=None)
    suspend_cause_exc_info: Optional[tuple[type, BaseException, TracebackType]] = event_field(default=None)

    def __post_init__(self) -> None:
        self.operation_name = self.operation
        self.tags[aws_durable.TAG_INVOKE_FUNCTION_NAME] = self.invoke_function_name


@dataclass
class AwsDurableOperationEvent(TracingEvent):
    event_name = AwsDurableEvents.OPERATION.value

    span_kind = SpanKind.INTERNAL
    span_type = SpanTypes.SERVERLESS

    operation: str = event_field()
    replayed: Optional[bool] = event_field(default=None)
    suspend_cause_exc_info: Optional[tuple[type, BaseException, TracebackType]] = event_field(default=None)

    def __post_init__(self) -> None:
        self.operation_name = self.operation
