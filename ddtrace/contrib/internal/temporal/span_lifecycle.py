"""Span lifecycle contract for the Temporal tracing interceptors."""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Mapping
from typing import Any
from typing import Protocol

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span

from .propagator import _Propagator
from .workflow_interceptor import WorkflowTracingConfig


class SpanLifecycle(Protocol):
    """The root interceptor's contract for the client, activity, and Nexus interceptors.

    ``DatadogTracingInterceptor`` implements this protocol structurally. The
    sub-interceptors annotate their ``root`` against this contract rather than
    importing the concrete class, which would create a circular import:
    ``interceptor`` imports the sub-interceptors to wire them into the Temporal
    client and worker.
    """

    propagator: _Propagator
    workflow_tracing_config: WorkflowTracingConfig

    def _start_span(
        self,
        *,
        operation_name: str,
        parent_ctx: Span | Context | None,
        resource_name: str,
        activate: bool,
        start_time: int | None = None,
        span_id: int | None = None,
        attributes: Mapping[str, Any] | None = None,
        parent_from_header: bool = False,
        trace_id: int | None = None,
    ) -> Span: ...

    async def _run_span(self, span: Span, operation_name: str, operation: Awaitable[Any]) -> Any: ...
