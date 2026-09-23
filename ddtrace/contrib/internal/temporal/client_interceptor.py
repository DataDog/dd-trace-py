"""Datadog tracing interceptor for Temporal client outbound calls."""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from typing import TYPE_CHECKING
from typing import Any
from ddtrace.contrib.internal.temporal.constants import TemporalOperationNames

import temporalio.client
from ddtrace.internal.settings._config import config
from ddtrace.contrib._events.temporal import TemporalWorkflowEvent
from ddtrace.internal import core


if TYPE_CHECKING:
    from .interceptor import DatadogTracingInterceptor


class _ClientOutboundInterceptor(temporalio.client.OutboundInterceptor):  # type: ignore[misc]
    def __init__(
        self,
        next: temporalio.client.OutboundInterceptor,
        root: DatadogTracingInterceptor,
    ) -> None:
        super().__init__(next)
        self.root = root

    async def start_workflow(
        self, input: temporalio.client.StartWorkflowInput
    ) -> temporalio.client.WorkflowHandle[Any, Any]:

        with core.context_with_event(
            TemporalWorkflowEvent(
                operation=TemporalOperationNames.SIGNAL_WITH_START_WORKFLOW if input.start_signal else TemporalOperationNames.START_WORKFLOW,
                resource=input.workflow,
                integration_config=config.temporal,
                component=config.integration_name,
                input=input
            )
        ):
            input.headers = self.root.propagator.inject_headers(input.headers, event.outgoing_carrier)
            return await super().start_workflow(input)

    async def signal_workflow(self, input: temporalio.client.SignalWorkflowInput) -> None:
        if self.root.disable_signal_tracing:
            await super().signal_workflow(input)
            return

        with core.context_with_event(
            TemporalWorkflowEvent(
                operation=TemporalOperationNames.SIGNAL_WORKFLOW,
                resource=input.signal,
                integration_config=config.temporal,
                component=config.integration_name,
                input=input
            )
        ):
            input.headers = self.root.propagator.inject_headers(input.headers, event.outgoing_carrier)
            await super().signal_workflow(input)

    async def query_workflow(self, input: temporalio.client.QueryWorkflowInput) -> Any:
        if self.root.disable_query_tracing:
            return await super().query_workflow(input)

        with core.context_with_event(
            TemporalWorkflowEvent(
                operation=TemporalOperationNames.QUERY_WORKFLOW,
                resource=input.query,
                integration_config=config.temporal,
                component=config.integration_name,
                input=input
            )
        ):
            input.headers = self.root.propagator.inject_headers(input.headers, event.outgoing_carrier)
            return await super().query_workflow(input)

    async def start_workflow_update(
        self, input: temporalio.client.StartWorkflowUpdateInput
    ) -> temporalio.client.WorkflowUpdateHandle[Any]:
        if self.root.disable_update_tracing:
            return await super().start_workflow_update(input)

        with core.context_with_event(
            TemporalWorkflowEvent(
                operation=TemporalOperationNames.UPDATE_WORKFLOW,
                resource=input.update,
                integration_config=config.temporal,
                component=config.integration_name,
                input=input
            )
        ):
            input.headers = self.root.propagator.inject_headers(input.headers, event.outgoing_carrier)
            return await super().start_workflow_update(input)

    async def create_schedule(self, input: temporalio.client.CreateScheduleInput) -> temporalio.client.ScheduleHandle:
        event = self.root._operation_event(
            operation_name=OperationNames.CREATE_SCHEDULE,
            resource_name=input.id,
            activate=True,
            use_active_context=True,
        )
        with core.context_with_event(event):
            return await super().create_schedule(input)

    async def start_update_with_start_workflow(
        self, input: temporalio.client.StartWorkflowUpdateWithStartInput
    ) -> temporalio.client.WorkflowUpdateHandle[Any]:
        if self.root.disable_update_tracing:
            # Update tracing is disabled, but this call also starts a new workflow.
            # Propagate the currently active trace into the workflow start headers so
            # RunWorkflow is not an unparented root when workflow tracing is enabled.
            propagation_event = TemporalPropagationEvent(use_active_context=True)
            core.dispatch_event(propagation_event)
            input.start_workflow_input.headers = self.root.propagator.inject_headers(
                input.start_workflow_input.headers, propagation_event.outgoing_carrier
            )
            return await super().start_update_with_start_workflow(input)

        operation_name = OperationNames.UPDATE_WITH_START_WORKFLOW
        attributes = self._get_workflow_attributes(input.start_workflow_input)
        attributes[SpanAttributes.UPDATE_NAME] = input.update_workflow_input.update
        if input.update_workflow_input.update_id:
            attributes[SpanAttributes.UPDATE_ID] = input.update_workflow_input.update_id
        event = self.root._operation_event(
            operation_name=operation_name,
            resource_name=input.update_workflow_input.update,
            activate=True,
            use_active_context=True,
            attributes=attributes,
            inject=True,
        )
        with core.context_with_event(event):
            input.start_workflow_input.headers = self.root.propagator.inject_headers(
                input.start_workflow_input.headers, event.outgoing_carrier
            )
            input.update_workflow_input.headers = self.root.propagator.inject_headers(
                input.update_workflow_input.headers, event.outgoing_carrier
            )
            return await super().start_update_with_start_workflow(input)

    async def start_activity(
        self, input: temporalio.client.StartActivityInput
    ) -> temporalio.client.ActivityHandle[Any]:
        return await self._start_operation(
            OperationNames.START_ACTIVITY,
            input,
            input.activity_type,
            self._get_activity_attributes(input),
            super().start_activity,
        )

    async def _start_operation(
        self,
        operation_name: str,
        input: Any,
        resource_name: str,
        attributes: dict[str, Any],
        awaitable: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        event = self.root._operation_event(
            operation_name=operation_name,
            resource_name=resource_name,
            activate=True,
            use_active_context=True,
            attributes=attributes,
            inject=True,
        )
        with core.context_with_event(event):
            input.headers = self.root.propagator.inject_headers(input.headers, event.outgoing_carrier)
            return await awaitable(input)

    @classmethod
    def _get_workflow_attributes(cls, input: Any) -> dict[str, Any]:
        attributes: dict[str, Any] = {SpanAttributes.WORKFLOW_ID: input.id}
        for field, span_key in COMMON_ATTRIBUTE_MAP:
            if val := getattr(input, field, None):
                attributes[span_key] = val
        if getattr(input, "workflow", None):
            attributes[SpanAttributes.WORKFLOW_TYPE] = input.workflow
        if getattr(input, "update", None):
            attributes[SpanAttributes.UPDATE_NAME] = input.update
        if getattr(input, "update_id", None):
            attributes[SpanAttributes.UPDATE_ID] = input.update_id
        return attributes

    @classmethod
    def _get_activity_attributes(cls, input: Any) -> dict[str, Any]:
        return {
            SpanAttributes.ACTIVITY_ID: input.id,
            SpanAttributes.ACTIVITY_TYPE: input.activity_type,
        }
