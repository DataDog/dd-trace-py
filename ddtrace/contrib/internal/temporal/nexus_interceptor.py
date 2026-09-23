"""Datadog tracing interceptor for Temporal Nexus operation inbound calls."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import nexusrpc.handler
import temporalio.worker

from ddtrace.contrib._events.temporal import OperationNames
from ddtrace.contrib._events.temporal import SpanAttributes
from ddtrace.internal import core


if TYPE_CHECKING:
    from .interceptor import DatadogTracingInterceptor


class _NexusOperationInboundInterceptor(
    temporalio.worker.NexusOperationInboundInterceptor,  # type: ignore[misc]
):
    def __init__(
        self,
        next: temporalio.worker.NexusOperationInboundInterceptor,
        root: DatadogTracingInterceptor,
    ) -> None:
        super().__init__(next)
        self.root = root

    async def execute_nexus_operation_start(
        self, input: temporalio.worker.ExecuteNexusOperationStartInput
    ) -> nexusrpc.handler.StartOperationResultSync[Any] | nexusrpc.handler.StartOperationResultAsync:
        return await self._run(input, OperationNames.RUN_NEXUS_OPERATION_START_HANDLER, True)

    async def execute_nexus_operation_cancel(self, input: temporalio.worker.ExecuteNexusOperationCancelInput) -> None:
        await self._run(input, OperationNames.RUN_NEXUS_OPERATION_CANCEL_HANDLER, False)

    async def _run(self, input: Any, operation_name: str, start: bool) -> Any:
        event = self.root._operation_event(
            operation_name=operation_name,
            incoming_carrier=input.ctx.headers,
            resource_name=f"{input.ctx.service}/{input.ctx.operation}",
            activate=True,
            use_active_context=False,
            attributes=self._get_nexus_attributes(input.ctx),
            parent_from_header=True,
        )
        with core.context_with_event(event, allow_raise=True):
            if start:
                return await super().execute_nexus_operation_start(input)
            return await super().execute_nexus_operation_cancel(input)

    @staticmethod
    def _get_nexus_attributes(nexus_ctx: Any) -> dict[str, Any]:
        return {
            SpanAttributes.NEXUS_SERVICE: nexus_ctx.service,
            SpanAttributes.NEXUS_OPERATION: nexus_ctx.operation,
        }
