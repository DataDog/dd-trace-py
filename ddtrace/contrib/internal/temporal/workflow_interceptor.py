"""Datadog tracing interceptors for Temporal workflow inbound and outbound calls."""

from collections.abc import Callable
from collections.abc import Generator
from contextlib import contextmanager
import contextvars
import logging
from typing import Any
from typing import NoReturn
from typing import cast

import temporalio.worker
import temporalio.workflow

from ddtrace.contrib._events.temporal import COMMON_ATTRIBUTE_MAP
from ddtrace.contrib._events.temporal import OperationNames
from ddtrace.contrib._events.temporal import SpanAttributes
from ddtrace.contrib._events.temporal import TemporalActivateWorkflowEvent
from ddtrace.contrib._events.temporal import TemporalPropagationEvent
from ddtrace.contrib._events.temporal import TemporalWorkflowLogEvent
from ddtrace.internal import core
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.fnv import fnv1_64

from .propagator import _Propagator


# ContextVars keep workflow state task-local. Sandboxed workflows load
# non-passthrough modules into a per-instance module namespace; unsandboxed
# workflow tasks capture their own context.
_active_workflow_context: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "_active_workflow_context", default=None
)
_trace_disconnected: contextvars.ContextVar[bool] = contextvars.ContextVar("_trace_disconnected", default=False)


class _DDTraceLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        core.dispatch_event(TemporalWorkflowLogEvent(record=record))
        return True


# Each sandbox instance can import this module again; avoid adding the same
# filter repeatedly to Temporal's shared workflow logger.
if not getattr(temporalio.workflow.logger.base_logger, "_dd_trace_filter_installed", False):
    temporalio.workflow.logger.base_logger.addFilter(_DDTraceLogFilter())
    temporalio.workflow.logger.base_logger._dd_trace_filter_installed = True


class DatadogTracingWorkflowInboundInterceptor(temporalio.worker.WorkflowInboundInterceptor):  # type: ignore[misc]
    def __init__(self, next: temporalio.worker.WorkflowInboundInterceptor) -> None:
        super().__init__(next)
        self._operation_counter = 1  # Reserve 1 for RunWorkflow's idempotency key.

        externs = temporalio.workflow.extern_functions()
        self._start_event_extern = cast(
            Callable[
                [str, str, dict[str, Any], Any | None, Any | None, int | None, str | None, int | None],
                tuple[Any, dict[str, str]],
            ],
            externs["__temporal_datadog_start_sandboxed_event"],
        )
        self._finish_event_extern = cast(
            Callable[[Any | None, BaseException | None], None],
            externs["__temporal_datadog_finish_sandboxed_event"],
        )
        config_func = cast(
            Callable[[], tuple[_Propagator, bool, bool, bool]],
            externs["__temporal_datadog_configure_workflow_tracing"],
        )
        (
            self.propagator,
            self.disable_signal_tracing,
            self.disable_query_tracing,
            self.disable_update_tracing,
        ) = config_func()

    def init(self, outbound: temporalio.worker.WorkflowOutboundInterceptor) -> None:
        super().init(_WorkflowOutboundInterceptor(outbound, self))

    @contextmanager
    def operation_ctx(
        self,
        operation_name: str,
        resource_name: str,
        input: Any,
        idempotency_key: str | None = None,
        start_time: int | None = None,
    ) -> Generator[tuple[Any, dict[str, str], Any | None], None, None]:
        attributes = self._get_span_attributes(input)
        incoming_carrier, workflow_carrier, workflow_span_id = self._parent_carriers_for(operation_name, input)

        # Idempotency-keyed HandleSignal and HandleUpdate spans are suppressed
        # while replaying. RunWorkflow is exempt so a restarted worker recreates
        # its long-lived span. HandleQuery and ValidateUpdate pass no idempotency
        # key, so this guard does not suppress them.
        if (
            idempotency_key is not None
            and operation_name != OperationNames.RUN_WORKFLOW
            and temporalio.workflow.unsafe.is_replaying()
        ):
            ctx = None
            outgoing_carrier: dict[str, str] = {}
        else:
            ctx, outgoing_carrier = self._start_event_extern(
                operation_name,
                resource_name,
                attributes,
                incoming_carrier,
                workflow_carrier,
                workflow_span_id,
                idempotency_key,
                start_time,
            )
        exc: BaseException | None = None
        try:
            yield input, outgoing_carrier, ctx
        except BaseException as e:
            exc = e
            raise
        finally:
            self._finish_event_extern(ctx, exc)

    def _parent_carriers_for(self, operation_name: str, input: Any) -> tuple[Any, Any, int | None]:
        # Parent from workflow header
        if operation_name == OperationNames.RUN_WORKFLOW:
            return self.propagator.extract_headers(temporalio.workflow.info().headers), None, None

        # Parent from input headers
        incoming = self.propagator.extract_headers(input.headers)
        workflow = self.propagator.extract_headers(temporalio.workflow.info().headers)
        return incoming, workflow, fnv1_64(self._make_idempotency_key(1).encode())

    def workflow_carrier(self) -> dict[str, str]:
        event = TemporalPropagationEvent(
            incoming_carrier=self.propagator.extract_headers(temporalio.workflow.info().headers),
            workflow_span_id=fnv1_64(self._make_idempotency_key(1).encode()),
        )
        core.dispatch_event(event, allow_raise=True)
        return event.outgoing_carrier

    def _get_span_attributes(self, input: Any) -> dict[str, Any]:
        info = temporalio.workflow.info()
        attrs: dict[str, Any] = {
            SpanAttributes.WORKFLOW_ID: info.workflow_id,
            SpanAttributes.RUN_ID: info.run_id,
            SpanAttributes.WORKFLOW_TYPE: info.workflow_type,
            SpanAttributes.NAMESPACE: info.namespace,
        }
        for field, span_key in COMMON_ATTRIBUTE_MAP:
            if val := getattr(input, field, None):
                attrs[span_key] = val
        if getattr(input, "update", None):
            attrs[SpanAttributes.UPDATE_NAME] = input.update
            if getattr(input, "id", None):
                attrs[SpanAttributes.UPDATE_ID] = input.id
        if getattr(input, "workflow", None):
            attrs[SpanAttributes.CHILD_WORKFLOW_TYPE] = input.workflow
            if getattr(input, "id", None):
                attrs[SpanAttributes.CHILD_WORKFLOW_ID] = input.id
        if isinstance(input, temporalio.worker.StartLocalActivityInput):
            attrs[SpanAttributes.LOCAL] = True
        return attrs

    def _make_idempotency_key(self, counter: int) -> str:
        info = temporalio.workflow.info()
        # Matches the Go SDK's idempotency key
        return f"WorkflowInboundInterceptor:{info.namespace}:{info.workflow_id}:{info.run_id}:{counter}"

    def _next_idempotency_key(self) -> str:
        self._operation_counter += 1
        return self._make_idempotency_key(self._operation_counter)

    async def execute_workflow(self, input: temporalio.worker.ExecuteWorkflowInput) -> Any:
        info = temporalio.workflow.info()
        with self.operation_ctx(
            OperationNames.RUN_WORKFLOW,
            info.workflow_type,
            input,
            idempotency_key=self._make_idempotency_key(1),
            start_time=int(info.workflow_start_time.timestamp() * 1e9),
        ) as (i, carrier, ctx):
            if ctx is not None:
                core.dispatch_event(TemporalActivateWorkflowEvent(operation_context=ctx))
            # AIDEV-NOTE: The RunWorkflow ExecutionContext owns its span. Keep
            # the context task-local so workflow helpers can resolve that span
            # without using an event as a request/response transport.
            token = _active_workflow_context.set(ctx)
            try:
                i.headers = self.propagator.inject_headers(i.headers, carrier)
                return await super().execute_workflow(i)
            finally:
                _active_workflow_context.reset(token)

    async def handle_signal(self, input: temporalio.worker.HandleSignalInput) -> None:
        if self.disable_signal_tracing:
            await super().handle_signal(input)
            return
        with self.operation_ctx(OperationNames.HANDLE_SIGNAL, input.signal, input, self._next_idempotency_key()) as (
            i,
            _,
            _,
        ):
            await super().handle_signal(i)

    async def handle_query(self, input: temporalio.worker.HandleQueryInput) -> Any:
        if self.disable_query_tracing:
            return await super().handle_query(input)
        with self.operation_ctx(OperationNames.HANDLE_QUERY, input.query, input) as (i, _, _):
            return await super().handle_query(i)

    def handle_update_validator(self, input: temporalio.worker.HandleUpdateInput) -> None:
        if self.disable_update_tracing:
            super().handle_update_validator(input)
            return
        with self.operation_ctx(OperationNames.VALIDATE_UPDATE, input.update, input) as (i, _, _):
            super().handle_update_validator(i)

    async def handle_update_handler(self, input: temporalio.worker.HandleUpdateInput) -> Any:
        if self.disable_update_tracing:
            return await super().handle_update_handler(input)
        with self.operation_ctx(OperationNames.HANDLE_UPDATE, input.update, input, self._next_idempotency_key()) as (
            i,
            _,
            _,
        ):
            return await super().handle_update_handler(i)


class _WorkflowOutboundInterceptor(temporalio.worker.WorkflowOutboundInterceptor):  # type: ignore[misc]
    def __init__(
        self,
        next: temporalio.worker.WorkflowOutboundInterceptor,
        root: DatadogTracingWorkflowInboundInterceptor,
    ) -> None:
        super().__init__(next)
        self.root = root

    def continue_as_new(self, input: temporalio.worker.ContinueAsNewInput) -> NoReturn:
        if not _trace_disconnected.get():
            input.headers = self.root.propagator.inject_headers(input.headers, self.root.workflow_carrier())
        super().continue_as_new(input)
        raise AssertionError("unreachable: continue_as_new did not raise")

    async def signal_child_workflow(self, input: temporalio.worker.SignalChildWorkflowInput) -> None:
        if self.root.disable_signal_tracing:
            await super().signal_child_workflow(input)
            return
        if temporalio.workflow.unsafe.is_replaying():
            await super().signal_child_workflow(input)
            return
        with self.root.operation_ctx(OperationNames.SIGNAL_CHILD_WORKFLOW, input.signal, input) as (i, carrier, _):
            i.headers = self.root.propagator.inject_headers(i.headers, carrier)
            await super().signal_child_workflow(i)

    async def signal_external_workflow(self, input: temporalio.worker.SignalExternalWorkflowInput) -> None:
        if self.root.disable_signal_tracing:
            await super().signal_external_workflow(input)
            return
        if temporalio.workflow.unsafe.is_replaying():
            await super().signal_external_workflow(input)
            return
        with self.root.operation_ctx(OperationNames.SIGNAL_EXTERNAL_WORKFLOW, input.signal, input) as (i, carrier, _):
            i.headers = self.root.propagator.inject_headers(i.headers, carrier)
            await super().signal_external_workflow(i)

    def start_activity(self, input: temporalio.worker.StartActivityInput) -> temporalio.workflow.ActivityHandle:
        if temporalio.workflow.unsafe.is_replaying():
            return super().start_activity(input)
        with self.root.operation_ctx(OperationNames.START_ACTIVITY, input.activity, input) as (i, carrier, _):
            i.headers = self.root.propagator.inject_headers(i.headers, carrier)
            return super().start_activity(i)

    async def start_child_workflow(
        self, input: temporalio.worker.StartChildWorkflowInput
    ) -> temporalio.workflow.ChildWorkflowHandle:
        if temporalio.workflow.unsafe.is_replaying():
            return await super().start_child_workflow(input)
        with self.root.operation_ctx(OperationNames.START_CHILD_WORKFLOW, input.workflow, input) as (i, carrier, _):
            i.headers = self.root.propagator.inject_headers(i.headers, carrier)
            return await super().start_child_workflow(i)

    def start_local_activity(
        self, input: temporalio.worker.StartLocalActivityInput
    ) -> temporalio.workflow.ActivityHandle:
        if temporalio.workflow.unsafe.is_replaying():
            return super().start_local_activity(input)
        with self.root.operation_ctx(OperationNames.START_ACTIVITY, input.activity, input) as (i, carrier, _):
            i.headers = self.root.propagator.inject_headers(i.headers, carrier)
            return super().start_local_activity(i)

    async def start_nexus_operation(
        self, input: temporalio.worker.StartNexusOperationInput[Any, Any]
    ) -> temporalio.workflow.NexusOperationHandle[Any]:
        # Skip Nexus tracing during workflow replay so replay does not emit a
        # duplicate StartNexusOperation span.
        if temporalio.workflow.unsafe.is_replaying():
            return await super().start_nexus_operation(input)

        with self.root.operation_ctx(
            OperationNames.START_NEXUS_OPERATION,
            f"{input.service}/{input.operation_name}",
            input,
        ) as (i, carrier, _):
            # Nexus uses plain string headers, not Temporal payload headers.
            i.headers = {**(i.headers or {}), **carrier}
            return await super().start_nexus_operation(i)


def span_from_workflow_context() -> Any:
    """Return the active RunWorkflow ddtrace span for this execution.

    Always returns a live span, including during replay on a new worker, so
    custom tags set here survive a worker restart::

        span = span_from_workflow_context()
        if span is not None:
            span.set_tag("my.tag", value)

    Python equivalent of the Go SDK's ``SpanFromWorkflowContext``.  Unlike the
    Go version, which takes a ``workflow.Context`` and can return any
    operation's span, this always returns the RunWorkflow span.
    """
    context = _active_workflow_context.get()
    return span_from_context(context) if context is not None else None


def disconnect_trace_span_from_workflow_context() -> None:
    """Prevent the current trace from propagating into the next ContinueAsNew execution.

    Call before ``workflow.continue_as_new()``; the next run starts a fresh
    root span rather than continuing this trace::

        disconnect_trace_span_from_workflow_context()
        workflow.continue_as_new(count + 1)

    """
    _trace_disconnected.set(True)
