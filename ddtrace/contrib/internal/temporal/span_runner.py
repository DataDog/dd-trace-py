"""Base span lifecycle management class for Datadog interceptors."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING
from typing import Any


if TYPE_CHECKING:
    from .interceptor import DatadogTracingInterceptor


class _SpanRunner:
    def __init__(self, root: DatadogTracingInterceptor) -> None:
        self.root = root

    async def run(self, span: Any, operation_name: str, operation: Awaitable[Any]) -> Any:
        operation_exc: BaseException | None = None
        try:
            result = await operation
        except BaseException as exc:
            operation_exc = exc
            raise
        finally:
            self.root.tracer.finish_span(span, operation_name, operation_exc)
        return result
