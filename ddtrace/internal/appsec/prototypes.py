import typing as t


class AppsecSpanProcessorProto(t.Protocol):
    def _update_rules(
        self,
        removals: t.Sequence[tuple[str, str]],
        updates: t.Sequence[tuple[str, str, t.Optional[dict[str, t.Any]]]],
    ) -> bool: ...

    def on_span_start(self, span: t.Any) -> None: ...

    def on_span_finish(self, span: t.Any) -> None: ...

    def shutdown(self, timeout: t.Optional[float]) -> None: ...
