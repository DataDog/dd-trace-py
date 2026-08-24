import typing as t

from ddtrace._trace.context import Context
from ddtrace.internal.compat import NumericType


class SpanProtocol(t.Protocol):
    """Structural interface for the span members ``ddtrace.appsec.*`` relies on.

    Lets appsec code type-annotate spans without a runtime dependency on the concrete
    ``ddtrace._trace.span.Span`` class.
    """

    name: str
    span_id: int
    span_type: t.Optional[str]

    @property
    def _parent(self) -> t.Optional["SpanProtocol"]: ...

    @property
    def _service_entry_span(self) -> "SpanProtocol": ...

    @property
    def context(self) -> Context: ...

    def get_tag(self, key: str) -> t.Optional[str]: ...

    def get_metric(self, key: str) -> t.Optional[NumericType]: ...

    def set_tag(self, key: str, value: t.Optional[str] = None) -> None: ...

    def _has_attribute(self, key: str) -> bool: ...

    def _set_attribute(self, key: str, value: t.Union[str, int, float]) -> None: ...

    def _set_struct_tag(self, key: str, value: dict[str, t.Any]) -> None: ...

    def _get_struct_tag(self, key: str) -> t.Optional[dict[str, t.Any]]: ...

    def _override_sampling_decision(self, decision: t.Optional[NumericType]) -> None: ...


class AppsecSpanProcessorProto(t.Protocol):
    def _update_rules(
        self,
        removals: t.Sequence[tuple[str, str]],
        updates: t.Sequence[tuple[str, str, t.Optional[dict[str, t.Any]]]],
    ) -> bool: ...

    def on_span_start(self, span: t.Any) -> None: ...

    def on_span_finish(self, span: t.Any) -> None: ...

    def shutdown(self, timeout: t.Optional[float]) -> None: ...
