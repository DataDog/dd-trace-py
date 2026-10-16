from types import TracebackType
from typing import Any
from typing import Optional
from typing import Protocol

from ddtrace.internal.native._native import Context


class _LLMObsSpan(Protocol):
    """Structural span interface shared by all LLMObs code.

    Lets LLMObs type-annotate spans without a runtime dependency on the concrete
    ddtrace._trace.span.Span class. Extend it with whatever members new call sites need.

    A bare SpanData (which lacks _set_ctx_item) does not satisfy this protocol, so passing one
    where an LLMObs span is expected is a type error instead of a silently swallowed exception.
    """

    span_type: Optional[str]
    error: int
    span_id: int
    trace_id: int
    start_ns: int

    @property
    def duration_ns(self) -> Optional[int]: ...

    @property
    def context(self) -> Context: ...

    @property
    def _parent(self) -> Optional["_LLMObsSpan"]: ...

    @property
    def _local_root(self) -> "_LLMObsSpan": ...

    def finish(self, finish_time: Optional[float] = None) -> None: ...

    def set_tag(self, key: str, value: Optional[str] = None) -> None: ...

    def set_exc_info(
        self,
        exc_type: type[BaseException],
        exc_val: BaseException,
        exc_tb: Optional[TracebackType],
        limit: Optional[int] = None,
    ) -> None: ...

    def _get_struct_tag(self, key: str) -> Optional[dict[str, Any]]: ...

    def _set_struct_tag(self, key: str, value: dict[str, Any]) -> None: ...

    def _remove_struct_tag(self, key: str) -> Optional[dict[str, Any]]: ...

    def _get_ctx_item(self, key: str) -> Optional[Any]: ...

    def _set_ctx_item(self, key: str, val: Any) -> None: ...
