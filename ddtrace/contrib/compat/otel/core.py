import contextvars
from typing import TYPE_CHECKING

import ddtrace.internal.core as _base_core
from ddtrace.internal.core import ExecutionContext as BaseExecutionContext


if TYPE_CHECKING:
    from opentelemetry.trace import Span as OtelSpan

ROOT_CONTEXT_ID = "__root"


class ExecutionContext(BaseExecutionContext):
    def __init__(self, identifier: str, parent: "ExecutionContext | None" = None, **kwargs) -> None:
        super().__init__(identifier, parent, **kwargs)
        self._inner_span: "OtelSpan | None" = None

    @property
    def span(self) -> "OtelSpan":  # type: ignore[override]
        return self._inner_span  # type: ignore[return-value]

    @span.setter
    def span(self, value: "OtelSpan") -> None:  # type: ignore[override]
        """Set the OTel span for this context."""
        self._inner_span = value  # type: ignore[assignment]


_CURRENT_CONTEXT = contextvars.ContextVar("ExecutionContext_var", default=ExecutionContext(ROOT_CONTEXT_ID))
_CONTEXT_CLASS = ExecutionContext


def context_with_data(identifier, parent=None, **kwargs):
    return _CONTEXT_CLASS(identifier, parent=(parent or _CURRENT_CONTEXT.get()), **kwargs)


def __getattr__(name: str):
    return getattr(_base_core, name)
