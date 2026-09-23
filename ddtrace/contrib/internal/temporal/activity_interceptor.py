"""Datadog tracing interceptor for Temporal activity inbound calls."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import temporalio.activity
import temporalio.worker

from ddtrace.contrib._events.temporal import OperationNames
from ddtrace.contrib._events.temporal import SpanAttributes
from ddtrace.internal import core


if TYPE_CHECKING:
    from .interceptor import DatadogTracingInterceptor


class _ActivityInboundInterceptor(temporalio.worker.ActivityInboundInterceptor):  # type: ignore[misc]
    def __init__(
        self,
        next: temporalio.worker.ActivityInboundInterceptor,
        root: DatadogTracingInterceptor,
    ) -> None:
        super().__init__(next)
        self.root = root

    async def execute_activity(self, input: temporalio.worker.ExecuteActivityInput) -> Any:
        info = temporalio.activity.info()
        event = self.root._operation_event(
            operation_name=OperationNames.RUN_ACTIVITY,
            incoming_carrier=self.root.propagator.extract_headers(input.headers),
            resource_name=info.activity_type,
            activate=True,
            use_active_context=False,
            idempotency_key=f"{info.workflow_run_id}:{info.activity_id}:{info.attempt}",
            attributes=self._get_activity_attributes(info),
            parent_from_header=True,
        )
        with core.context_with_event(event, allow_raise=True):
            return await super().execute_activity(input)

    @staticmethod
    def _get_activity_attributes(info: temporalio.activity.Info) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            SpanAttributes.ACTIVITY_ID: info.activity_id,
            SpanAttributes.ACTIVITY_TYPE: info.activity_type,
            SpanAttributes.ATTEMPT: info.attempt,
        }
        if info.workflow_id:
            attributes[SpanAttributes.WORKFLOW_ID] = info.workflow_id
        if info.workflow_run_id:
            attributes[SpanAttributes.RUN_ID] = info.workflow_run_id
        if info.workflow_namespace:
            attributes[SpanAttributes.NAMESPACE] = info.workflow_namespace
        return attributes
