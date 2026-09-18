"""Datadog tracing interceptor for Temporal."""

from collections.abc import Callable
from collections.abc import Mapping
from typing import Any

import temporalio.activity
import temporalio.client
import temporalio.converter
import temporalio.worker
import temporalio.workflow

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger
from ddtrace.trace import tracer

from .activity_interceptor import _ActivityInboundInterceptor
from .client_interceptor import _ClientOutboundInterceptor
from .constants import CONTINUE_AS_NEW_TAG
from .constants import DEFAULT_HEADER_KEY
from .constants import TEMPORAL_TAG_PREFIX
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


log = get_logger(__name__)


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

        self._service_name = service_name
        self._on_span_finish = on_span_finish
        self._annotator = _SpanAnnotator(service_name=service_name, extra_tags=extra_tags)

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
    ) -> Span:
        # AIDEV-NOTE: Supply deterministic trace IDs through the parent context;
        # changing span.trace_id after creation breaks the tracer's trace registry.
        effective_parent = parent_ctx
        if trace_id is not None and parent_ctx is None:
            effective_parent = Context(trace_id=trace_id, span_id=None, is_remote=True)
        span = tracer.start_span(
            name=f"{TEMPORAL_TAG_PREFIX}{operation_name}",
            child_of=effective_parent,
            service=self._service_name,
            resource=resource_name,
            activate=activate,
        )
        if start_time is not None:
            span.start_ns = start_time
        if span_id is not None:
            span.span_id = span_id
            span.context.span_id = span_id
        force_keep = parent_ctx is None or parent_from_header
        self._annotator.annotate(span, operation_name, attributes, self.propagator.get_baggage(parent_ctx), force_keep)
        self.propagator.set_baggage(span.context)
        return span

    def _finish_span(
        self,
        span: Span,
        operation_name: str,
        exc: BaseException | None,
    ) -> None:
        try:
            result: FinishResult | None = None
            if self._on_span_finish is not None:
                try:
                    result = self._on_span_finish(FinishContext(operation=operation_name, exception=exc))
                except Exception:
                    log.error(
                        "temporal on_span_finish callback for %r raised; ignoring",
                        operation_name,
                        exc_info=True,
                    )

            if exc and not self._should_skip_error(exc):
                span.set_exc_info(type(exc), exc, exc.__traceback__)

            if result is not None and result.extra_tags:
                for key, value in result.extra_tags.items():
                    span.set_tag(key, value)
        finally:
            span.finish()

    def _should_skip_error(self, exc: BaseException | None) -> bool:
        if exc is None:
            return True
        if isinstance(exc, temporalio.workflow.ContinueAsNewError):
            return True
        if isinstance(exc, temporalio.activity._CompleteAsyncError):
            return True
        return False

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
        span = self._start_span(
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

        self._finish_span(span, operation_name, operation_exc)


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
