from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Optional

from ddtrace._trace.context import Context
from ddtrace._trace.events import TracingEvent
from ddtrace.contrib.internal.ray.constants import RAY_GET_VALUE_SIZE_BYTES
from ddtrace.contrib.internal.ray.constants import RAY_PUT_VALUE_SIZE_BYTES
from ddtrace.contrib.internal.ray.constants import RAY_PUT_VALUE_TYPE
from ddtrace.contrib.internal.ray.constants import RAY_WAIT_FETCH_LOCAL
from ddtrace.contrib.internal.ray.constants import RAY_WAIT_NUM_RETURNS
from ddtrace.contrib.internal.ray.constants import RAY_WAIT_TIMEOUT
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


class RayEvents(Enum):
    RAY_JOB = "ray.job"
    RAY_EXECUTE = "ray.execute"
    RAY_CORE_API = "ray.core_api"
    RAY_SUBMIT = "ray.submit"
    RAY_CONTEXT_INJECTION = "ray.context.injection"


@dataclass
class RayJobEvent(TracingEvent):
    event_name = RayEvents.RAY_JOB.value

    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.RAY

    submission_id: str = event_field()
    job_name: str = event_field()
    entrypoint: Optional[str] = event_field(default=None)
    metadata: dict[str, str] = event_field()
    environment_variables: dict[str, Any] = event_field()
    submit_failed: bool = event_field(default=False)
    ended_job_info: Optional[Any] = event_field(default=None)

    # the span will be finish manually because this is a
    # long running span
    _end_span: bool = False

    def __post_init__(self) -> None:
        self.operation_name = self.event_name


@dataclass
class RayExecutionEvent(TracingEvent):
    """This event is used to represent when a remote task
    or a remote actor method is indeed executed.
    """

    event_name = RayEvents.RAY_EXECUTE.value

    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.RAY

    is_actor_method: bool = event_field(default=False)
    is_remote_task: bool = event_field(default=False)

    method_args: tuple[Any] = event_field()
    method_kwargs: dict[str, Any] = event_field()

    _end_span: bool = False

    def __post_init__(self) -> None:
        if self.is_actor_method:
            self.operation_name = "actor_method.execute"
        elif self.is_remote_task:
            self.operation_name = "task.execute"
        else:
            self.operation_name = self.event_name


@dataclass
class RayCoreAPIEvent(TracingEvent):
    event_name = RayEvents.RAY_CORE_API.value

    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.RAY

    api_name: str = event_field()
    is_long_running: bool = event_field(default=False)
    timeout_s: Optional[float] = event_field(default=None)
    num_returns: Optional[int] = event_field(default=None)
    fetch_local: Optional[bool] = event_field(default=None)
    get_value_size_bytes: Optional[int] = event_field(default=None)
    put_value_type: Optional[str] = event_field(default=None)
    put_value_size_bytes: Optional[int] = event_field(default=None)

    # spans for this event are finished manually by the subscriber
    _end_span: bool = False

    def __post_init__(self) -> None:
        self.operation_name = self.api_name

        if self.timeout_s is not None:
            self.tags[RAY_WAIT_TIMEOUT] = str(self.timeout_s)
        if self.num_returns is not None:
            self.tags[RAY_WAIT_NUM_RETURNS] = str(self.num_returns)
        if self.fetch_local is not None:
            self.tags[RAY_WAIT_FETCH_LOCAL] = str(self.fetch_local)
        if self.get_value_size_bytes is not None:
            self.tags[RAY_GET_VALUE_SIZE_BYTES] = str(self.get_value_size_bytes)
        if self.put_value_type is not None:
            self.tags[RAY_PUT_VALUE_TYPE] = self.put_value_type
        if self.put_value_size_bytes is not None:
            self.tags[RAY_PUT_VALUE_SIZE_BYTES] = str(self.put_value_size_bytes)


@dataclass
class RaySubmissionEvent(TracingEvent):
    event_name = RayEvents.RAY_SUBMIT.value

    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.RAY

    method_args: object = event_field(default=None)
    method_kwargs: object = event_field(default=None)
    is_actor_method: bool = event_field(default=False)
    is_task_submission: bool = event_field(default=False)

    def __post_init__(self) -> None:
        self.operation_name = "task.submit" if self.is_task_submission else "actor_method.submit"


@dataclass
class RayContextInjectionEvent(Event):
    event_name = RayEvents.RAY_CONTEXT_INJECTION.value

    kwargs: dict[str, Any] = event_field()
    current_context: Optional[Context] = event_field(default=None)
