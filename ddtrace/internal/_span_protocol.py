from typing import Any
from typing import Mapping
from typing import Optional
from typing import Protocol
from typing import Union
from typing import runtime_checkable


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

    @property
    def _trace_id_64bits(self) -> int: ...

    @property
    def _is_top_level(self) -> bool: ...

    def _get_str_attributes(self) -> Mapping[str, str]: ...

    def _get_numeric_attributes(self) -> Mapping[str, Union[int, float]]: ...

    def _has_links(self) -> bool: ...

    def _get_links(self) -> list[Any]: ...

    def _has_events(self) -> bool: ...

    def _get_events(self) -> list[Any]: ...

    def _get_meta_structs(self) -> dict[str, Any]: ...

    def _set_attribute(self, key: str, value: Union[str, int, float]) -> None: ...
