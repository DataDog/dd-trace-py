from typing import Any
from typing import Mapping
from typing import Optional
from typing import Protocol
from typing import Union
from typing import runtime_checkable

from ddtrace.internal.compat import NumericType


@runtime_checkable
class SpanContextProtocol(Protocol):
    """Structural contract for the context-like object exposed by ``SpanProtocol.context``.

    Kept independent of ``ddtrace._trace.context.Context`` to avoid pulling the tracing
    product into ``ddtrace.internal``; ``Context`` conforms to this shape.
    """

    dd_origin: Optional[str]

    @property
    def sampling_priority(self) -> Optional[NumericType]: ...

    @property
    def _tracestate(self) -> str: ...


@runtime_checkable
class SpanProtocol(Protocol):
    """Structural contract for the span-like objects the encoders serialize.

    Kept independent of ``ddtrace._trace.span.Span`` to avoid pulling the tracing
    product into ``ddtrace.internal``; ``Span`` conforms to this shape.
    """

    parent_id: Optional[int]
    span_id: int
    service: Optional[str]
    resource: str
    name: str
    error: int
    start_ns: int
    duration_ns: Optional[int]
    span_type: Optional[str]
    trace_id: int

    @property
    def _trace_id_64bits(self) -> int: ...

    @property
    def _is_top_level(self) -> bool: ...

    @property
    def _local_root(self) -> "SpanProtocol": ...

    @property
    def finished(self) -> bool: ...

    @property
    def context(self) -> SpanContextProtocol: ...

    def _get_str_attributes(self) -> Mapping[str, str]: ...

    def _get_numeric_attributes(self) -> Mapping[str, Union[int, float]]: ...

    def _has_links(self) -> bool: ...

    def _get_links(self) -> list[Any]: ...

    def _has_events(self) -> bool: ...

    def _get_events(self) -> list[Any]: ...

    def _get_meta_structs(self) -> dict[str, Any]: ...

    def _get_struct_tag(self, key: str) -> Optional[dict[str, Any]]: ...

    def _get_str_attribute(self, key: str) -> Optional[str]: ...

    def _get_ctx_item(self, key: str) -> Optional[Any]: ...

    def _set_ctx_item(self, key: str, val: Any) -> None: ...

    def _set_attribute(self, key: str, value: Union[str, int, float]) -> None: ...

    def _remove_attribute(self, key: str) -> None: ...

    def _finish_ns(self, finish_time_ns: int) -> None: ...

    def _add_event(
        self,
        name: str,
        attributes: Optional[Mapping[str, Any]] = None,
        time_unix_nano: Optional[int] = None,
    ) -> None: ...

    def set_tag(self, key: str, value: Optional[str] = None) -> None: ...

    def set_tags(self, tags: dict[str, str]) -> None: ...

    def get_tag(self, key: str) -> Optional[str]: ...

    def get_tags(self) -> dict[str, str]: ...
