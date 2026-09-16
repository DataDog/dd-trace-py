"""Datadog tracing interceptor for Temporal."""

from collections.abc import Callable
from collections.abc import Mapping
from typing import Any

import temporalio.client
import temporalio.converter
import temporalio.worker
import temporalio.workflow

from .activity_interceptor import _ActivityInboundInterceptor
from .client_interceptor import _ClientOutboundInterceptor
from .constants import CONTINUE_AS_NEW_TAG
from .constants import DEFAULT_HEADER_KEY
from .constants import OperationNames
from .id_generator import gen_span_id
from .id_generator import gen_trace_id
from .nexus_interceptor import _NexusOperationInboundInterceptor
from .propagator import _Propagator
from .span_annotator import _SpanAnnotator
from .workflow_interceptor import DatadogTracingWorkflowInboundInterceptor
from .workflow_interceptor import WorkflowTracingConfig
from .workflow_interceptor import _active_workflow_span
from .wrapped_tracer import FinishContext
from .wrapped_tracer import FinishResult
from .wrapped_tracer import WrappedTracer


class DatadogTracingInterceptor(temporalio.client.Interceptor, temporalio.worker.Interceptor):  # type: ignore[misc]
    def __init__(
        self,
        *,
        service_name: str | None = None,
        header_key: str = DEFAULT_HEADER_KEY,
        extra_tags: Mapping[str, str] | None = None,
        on_span_finish: Callable[[FinishContext], FinishResult | None] | None = None,
        workflow_tracing_config: WorkflowTracingConfig | None = None,
        allow_invalid_parent_spans: bool = False,
    ) -> None:
        self.workflow_tracing_config = workflow_tracing_config or WorkflowTracingConfig.default_config()

        _register_sandbox_passthrough()

        self.propagator = _Propagator(
            header_key=header_key,
            service_name=service_name,
            payload_converter=temporalio.converter.PayloadConverter.default,
            allow_invalid_parent_spans=allow_invalid_parent_spans,
        )

        self.tracer = WrappedTracer(
            service_name=service_name,
            on_span_finish=on_span_finish,
            annotator=_SpanAnnotator(service_name=service_name, extra_tags=extra_tags),
            propagator=self.propagator,
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
        input.unsafe_extern_functions["__temporal_datadog_start_sandboxed_span"] = self._start_sandboxed_span
        input.unsafe_extern_functions["__temporal_datadog_finish_sandboxed_span"] = self._finish_sandboxed_span
        input.unsafe_extern_functions["__temporal_datadog_configure_workflow_tracing"] = (
            self._configure_workflow_tracing
        )
        return DatadogTracingWorkflowInboundInterceptor

    def _configure_workflow_tracing(self) -> tuple[_Propagator, WorkflowTracingConfig]:
        return self.propagator, self.workflow_tracing_config

    def _start_sandboxed_span(
        self,
        operation_name: str,
        resource_name: str,
        attributes: dict[str, Any] | None,
        parent_ctx: Any | None,
        idempotency_key: str | None,
        start_time: int | None = None,
    ) -> Any:
        # No DD header (uninstrumented client): pass a deterministic trace_id to keep
        # the RunWorkflow trace consistent if the worker restarts mid-run.
        det_trace_id = (
            gen_trace_id(idempotency_key)
            if operation_name == OperationNames.RUN_WORKFLOW and parent_ctx is None and idempotency_key is not None
            else None
        )
        span = self.tracer.start_span(
            operation_name=operation_name,
            parent_ctx=parent_ctx,
            resource_name=resource_name,
            activate=False,
            start_time=start_time,
            span_id=gen_span_id(idempotency_key) if idempotency_key is not None else None,
            attributes=attributes,
            parent_from_header=True,
            trace_id=det_trace_id,
        )
        # Expose RunWorkflow to the host-side ContextVar that
        # span_from_workflow_context() returns via the extern above.
        if operation_name == OperationNames.RUN_WORKFLOW:
            _active_workflow_span.set(span)
        return span

    def _finish_sandboxed_span(
        self,
        operation_name: str,
        span: Any | None,
        operation_exc: BaseException | None,
    ) -> None:
        if span is None:
            return

        if isinstance(operation_exc, temporalio.workflow.ContinueAsNewError):
            span.set_tag(CONTINUE_AS_NEW_TAG, True)

        self.tracer.finish_span(span, operation_name, operation_exc)


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
