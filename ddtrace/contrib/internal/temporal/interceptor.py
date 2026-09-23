"""Datadog tracing interceptor for Temporal."""

from collections.abc import Callable
from collections.abc import Mapping
from typing import Any

import temporalio.activity
import temporalio.client
import temporalio.converter
import temporalio.worker
import temporalio.workflow

from ddtrace.contrib._events.temporal import FinishContext
from ddtrace.contrib._events.temporal import FinishResult
from ddtrace.contrib._events.temporal import TemporalOperationEvent
from ddtrace.internal import core
from ddtrace.internal.settings._config import config

from .activity_interceptor import _ActivityInboundInterceptor
from .client_interceptor import _ClientOutboundInterceptor
from .constants import DEFAULT_HEADER_KEY
from .nexus_interceptor import _NexusOperationInboundInterceptor
from .propagator import _Propagator
from .workflow_interceptor import DatadogTracingWorkflowInboundInterceptor


class DatadogTracingInterceptor(temporalio.client.Interceptor, temporalio.worker.Interceptor):  # type: ignore[misc]
    def __init__(
        self,
        *,
        service_name: str | None = config.temporal.service,
        header_key: str = DEFAULT_HEADER_KEY,
        extra_tags: Mapping[str, str] | None = None,
        on_span_finish: Callable[[FinishContext], FinishResult | None] | None = None,
        disable_signal_tracing: bool | None = config.temporal.disable_signal_tracing,
        disable_query_tracing: bool | None =  config.temporal.disable_query_tracing,
        disable_update_tracing: bool | None = config.temporal.disable_update_tracing,
        allow_invalid_parent_spans: bool = False,
    ) -> None:
        self.disable_signal_tracing = disable_signal_tracing
        self.disable_query_tracing = disable_query_tracing
        self.disable_update_tracing = disable_update_tracing

        _register_sandbox_passthrough()

        self.propagator = _Propagator(
            header_key=header_key,
            payload_converter=temporalio.converter.PayloadConverter.default,
            allow_invalid_parent_spans=allow_invalid_parent_spans,
        )

        self._service_name = service_name
        self._extra_tags = extra_tags or {}
        self._on_span_finish = on_span_finish
        self._allow_invalid_parent_spans = allow_invalid_parent_spans

    def _operation_event(
        self,
        *,
        operation_name: str,
        resource_name: str,
        activate: bool,
        use_active_context: bool,
        incoming_carrier: Mapping[str, str] | None = None,
        workflow_carrier: Mapping[str, str] | None = None,
        workflow_span_id: int | None = None,
        start_time: int | None = None,
        idempotency_key: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        parent_from_header: bool = False,
        inject: bool = False,
    ) -> TemporalOperationEvent:
        return TemporalOperationEvent(
            operation=operation_name,
            component="temporal",
            integration_config=config.temporal,
            service=self._service_name,
            resource=resource_name,
            activate=activate,
            use_active_context=use_active_context,
            measured=False,
            attributes=attributes or {},
            incoming_carrier=incoming_carrier,
            workflow_carrier=workflow_carrier,
            workflow_span_id=workflow_span_id,
            start_ns=start_time,
            idempotency_key=idempotency_key,
            deterministic_root_trace=operation_name == "RunWorkflow",
            inject=inject,
            parent_from_header=parent_from_header,
            allow_invalid_parent_spans=self._allow_invalid_parent_spans,
            extra_tags=self._extra_tags,
            on_span_finish=self._on_span_finish,
            ignored_exceptions=(temporalio.workflow.ContinueAsNewError, temporalio.activity._CompleteAsyncError),
            continued_as_new_exception=temporalio.workflow.ContinueAsNewError,
        )

    def intercept_client(self, next: temporalio.client.OutboundInterceptor) -> temporalio.client.OutboundInterceptor:
        return _ClientOutboundInterceptor(next, self)

    def intercept_activity(
        self, next: temporalio.worker.ActivityInboundInterceptor
    ) -> temporalio.worker.ActivityInboundInterceptor:
        return _ActivityInboundInterceptor(next, self)

    def intercept_nexus_operation(
        self, next: temporalio.worker.NexusOperationInboundInterceptor
    ) -> temporalio.worker.NexusOperationInboundInterceptor:
        return _NexusOperationInboundInterceptor(next, self)

    def workflow_interceptor_class(
        self, input: temporalio.worker.WorkflowInterceptorClassInput
    ) -> type[DatadogTracingWorkflowInboundInterceptor]:
        input.unsafe_extern_functions["__temporal_datadog_start_sandboxed_event"] = self._start_sandboxed_event
        input.unsafe_extern_functions["__temporal_datadog_finish_sandboxed_event"] = self._finish_sandboxed_event
        input.unsafe_extern_functions["__temporal_datadog_configure_workflow_tracing"] = (
            self._configure_workflow_tracing
        )
        return DatadogTracingWorkflowInboundInterceptor

    def _configure_workflow_tracing(self) -> tuple[_Propagator, bool, bool, bool]:
        return (
            self.propagator,
            self.disable_signal_tracing,
            self.disable_query_tracing,
            self.disable_update_tracing,
        )

    def _start_sandboxed_event(
        self,
        operation_name: str,
        resource_name: str,
        attributes: dict[str, Any] | None,
        incoming_carrier: Mapping[str, str] | None,
        workflow_carrier: Mapping[str, str] | None,
        workflow_span_id: int | None,
        idempotency_key: str | None,
        start_time: int | None = None,
    ) -> tuple[Any, dict[str, str]]:
        event = self._operation_event(
            operation_name=operation_name,
            resource_name=resource_name,
            activate=False,
            use_active_context=False,
            incoming_carrier=incoming_carrier,
            workflow_carrier=workflow_carrier,
            workflow_span_id=workflow_span_id,
            start_time=start_time,
            idempotency_key=idempotency_key,
            attributes=attributes,
            parent_from_header=True,
            inject=True,
        )
        ctx = core.context_with_event(event, dispatch_end_event=False, allow_raise=True)
        ctx.__enter__()
        ctx.__exit__(None, None, None)
        return ctx, event.outgoing_carrier

    def _finish_sandboxed_event(
        self,
        ctx: Any | None,
        operation_exc: BaseException | None,
    ) -> None:
        if ctx is None:
            return
        ctx.dispatch_ended_event(
            type(operation_exc) if operation_exc is not None else None,
            operation_exc,
            operation_exc.__traceback__ if operation_exc is not None else None,
        )


# The workflow sandbox re-imports every non-passthrough module fresh; doing
# so for ddtrace fails (asyncio-loop conflict at init, restricted builtins.open).
# Registering the ddtrace namespace as passthrough makes the sandbox reuse the
# host's already-imported modules instead.  Idempotent: mutated in place once.
_SANDBOX_PASSTHROUGH_MODULES: tuple[str, ...] = ("ddtrace",)
_passthrough_registered: bool = False


def _register_sandbox_passthrough() -> None:
    global _passthrough_registered
    if _passthrough_registered:
        return
    from temporalio.worker.workflow_sandbox._restrictions import SandboxRestrictions

    SandboxRestrictions.passthrough_modules_default.update(_SANDBOX_PASSTHROUGH_MODULES)
    _passthrough_registered = True
