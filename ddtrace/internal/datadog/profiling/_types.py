from typing import Optional
from typing import Protocol
from typing import Union


StringType = Union[None, str, bytes]


class _EndpointCallCounter(Protocol):
    def reset(self) -> tuple[dict[str, int], dict[str, list[int]]]: ...


class ProfilerTracer(Protocol):
    """The subset of ddtrace.trace.Tracer the profiler uses, so profiling does not import the tracing product."""

    @property
    def context_provider(self) -> object: ...

    @property
    def agent_trace_url(self) -> Optional[str]: ...

    @property
    def _endpoint_call_counter_span_processor(self) -> _EndpointCallCounter: ...
