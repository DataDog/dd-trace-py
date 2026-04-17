"""
Instrumentation for AWS Durable Execution SDK.

Traces durable operations (step, wait, invoke, etc.) and propagates trace context
across Lambda suspensions and replays via extra checkpoints.

Instead of injecting trace context into customer operation payloads, we create
additional STEP checkpoints alongside each completed operation. These extra
checkpoints store trace context in their own payload, leaving customer data
untouched. On subsequent invocations, the extractor reads the last trace
checkpoint to continue the trace.
"""

import contextvars
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Optional

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from wrapt import wrap_function_wrapper as _w


log = get_logger(__name__)


# Module-level configuration
_INTEGRATION_CONFIG = {
    "_default_service": "aws.durable-execution",
    "distributed_tracing": True,
}

config._add("aws_durable_execution_sdk_python", _INTEGRATION_CONFIG)


# Store reference to original ThreadPoolExecutor for unpatching
_OriginalThreadPoolExecutor = ThreadPoolExecutor

# Global reference to current ExecutionState for checkpoint creation from wrapper
_current_execution_state = None
# Global reference to the durable root span (set by wrapper._before, used by checkpoint)
_durable_root_span = None


def _get_current_execution_state():
    """Get the current ExecutionState instance for creating checkpoints."""
    return _current_execution_state


def set_durable_root_span(span):
    """Store the durable root span for checkpoint save during first create_checkpoint."""
    global _durable_root_span
    _durable_root_span = span


class TracedThreadPoolExecutor(ThreadPoolExecutor):
    """
    ThreadPoolExecutor that propagates trace context to worker threads.

    Captures context at init time as a fallback for re-submissions from
    background threads (e.g., SDK's TimerScheduler retries) that have no
    trace context. At submit time, if the current thread has an active trace
    context, it's used; otherwise, the init-time context is used.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Capture context at creation time (main thread with active span).
        # Used as fallback when submit() is called from background threads
        # (like TimerScheduler) that have no trace context.
        self._init_ctx = contextvars.copy_context()

    def submit(self, fn, /, *args, **kwargs):
        """Submit a task with trace context propagation."""
        from ddtrace import tracer

        # Use current context if it has an active trace; otherwise fall back
        # to the context captured at init time (handles re-submissions from
        # background threads like TimerScheduler).
        active = tracer.context_provider.active()
        if active is not None:
            ctx = contextvars.copy_context()
        else:
            # Create a fresh copy of the init-time context for each submission.
            # We cannot reuse self._init_ctx directly because Context.run() is
            # not reentrant — concurrent threads calling run() on the same
            # Context object raises RuntimeError.
            ctx = self._init_ctx.run(contextvars.copy_context)
            print(f"[DD-INTEGRATION] TracedThreadPoolExecutor.submit(): no active context, using init-time fallback")

        # Wrap the function to run in the captured context
        def wrapped_fn(*args, **kwargs):
            return ctx.run(fn, *args, **kwargs)

        # Submit the wrapped function to the thread pool
        return super().submit(wrapped_fn, *args, **kwargs)


def get_version():
    """Get the version of aws-durable-execution-sdk-python."""
    try:
        import aws_durable_execution_sdk_python
        return getattr(aws_durable_execution_sdk_python, "__version__", "")
    except (ImportError, AttributeError):
        return ""


def _build_trace_context_payload(span) -> Optional[str]:
    """
    Build a JSON payload containing the full trace context from the given span.

    Uses HTTPPropagator.inject() to capture all propagation headers including
    trace_id, span_id, sampling_priority, baggage, _dd.p.* tags, and origin.
    Returns a JSON string, or None if span is invalid.
    """
    if not span or not span.context:
        return None

    from ddtrace.propagation.http import HTTPPropagator

    headers = {}
    HTTPPropagator.inject(span.context, headers)

    if not headers.get("x-datadog-trace-id"):
        return None

    return json.dumps(headers, separators=(",", ":"))


def _inject_current_context() -> Optional[dict]:
    """
    Serialize the current active trace context into a headers dict.

    Used for snapshotting context to detect changes (Component 3).
    Returns the headers dict, or None if no active context.

    Falls back through: current_span → current_root_span → context_provider.active()
    The durable execution decorator may run in a context where spans aren't tracked
    as "current", but the Context object is still propagated via context_provider.
    """
    from ddtrace import tracer
    from ddtrace.propagation.http import HTTPPropagator

    # Try current span first (works when called from wrapper._after before span.finish())
    active = tracer.current_span()
    if active and active.context:
        headers = {}
        HTTPPropagator.inject(active.context, headers)
        return headers if headers.get("x-datadog-trace-id") else None

    # Try root span
    root = tracer.current_root_span()
    if root and root.context:
        headers = {}
        HTTPPropagator.inject(root.context, headers)
        return headers if headers.get("x-datadog-trace-id") else None

    # Fall back to context_provider.active() — this returns a Context object
    # (not a Span) but HTTPPropagator.inject() accepts it
    ctx = tracer.context_provider.active()
    if ctx is not None:
        print(f"[DD-INTEGRATION] _inject_current_context: using context_provider.active() fallback")
        headers = {}
        HTTPPropagator.inject(ctx, headers)
        return headers if headers.get("x-datadog-trace-id") else None

    print(f"[DD-INTEGRATION] _inject_current_context: no active context found")
    return None


def save_root_trace_checkpoint(state, root_span):
    """
    Component 2: Save the root span's trace context as an extra checkpoint.

    Called from wrapper._after() on first invocation only, after the execution
    loop has completed. This ensures the checkpoint batcher is active and ready.

    Args:
        state: ExecutionState instance
        root_span: The durable execution root span whose context to save
    """
    if not root_span:
        print("[DD-INTEGRATION] No root span to save as trace checkpoint")
        return

    trace_payload = _build_trace_context_payload(root_span)
    if not trace_payload:
        return

    try:
        from aws_durable_execution_sdk_python.lambda_service import (
            OperationUpdate as SdkOperationUpdate,
            OperationType,
            OperationAction,
        )
        import hashlib

        execution_arn = getattr(state, "durable_execution_arn", "unknown")
        checkpoint_id = hashlib.blake2b(
            f"_dd_root_trace_ctx_{execution_arn}".encode()
        ).hexdigest()[:64]

        start_update = SdkOperationUpdate(
            operation_id=checkpoint_id,
            operation_type=OperationType.STEP,
            action=OperationAction.START,
            name="_dd_trace_context",
        )
        state.create_checkpoint(start_update, is_sync=True)

        succeed_update = SdkOperationUpdate(
            operation_id=checkpoint_id,
            operation_type=OperationType.STEP,
            action=OperationAction.SUCCEED,
            name="_dd_trace_context",
            payload=trace_payload,
        )
        state.create_checkpoint(succeed_update, is_sync=True)

        # Store the root span's headers as the "before" state for Component 3.
        # On first invocation there's no checkpoint in the event, so this is
        # the baseline for detecting context changes (e.g., customer adds baggage).
        state._dd_before_trace_headers = json.loads(trace_payload)

        print(f"[DD-INTEGRATION] Saved root trace checkpoint: trace_id={root_span.trace_id}, span_id={root_span.span_id}")
        log.debug(
            "[RequestID: %s] Saved root trace checkpoint for execution %s",
            getattr(state, "_dd_aws_request_id", "unknown"),
            execution_arn,
        )
    except Exception as e:
        # Non-fatal: reset checkpointing_failed flag if set
        try:
            if hasattr(state, "_checkpointing_failed") and state._checkpointing_failed.is_set():
                state._checkpointing_failed.clear()
                print("[DD-INTEGRATION] Reset _checkpointing_failed flag after root trace checkpoint failure")
        except Exception:
            pass
        print(f"[DD-INTEGRATION] Failed to save root trace checkpoint (non-fatal): {e}")
        log.debug("Failed to save root trace checkpoint: %s", e)


def _extract_trace_headers_from_event_args(args) -> Optional[dict]:
    """
    Scan the ExecutionState.__init__ args for the event dict and extract
    the latest _dd_trace_context checkpoint payload.

    Returns the parsed headers dict, or None if not found.
    This is the "before" state for Component 3 change detection.
    """
    # Find the event dict in args (first dict with InitialExecutionState)
    event = None
    for arg in args:
        if isinstance(arg, dict) and "InitialExecutionState" in arg:
            event = arg
            break
    if not event:
        return None

    operations = event.get("InitialExecutionState", {}).get("Operations", [])
    if not operations:
        return None

    # Find the latest _dd_trace_context checkpoint (highest index = latest event ID)
    for op in reversed(operations):
        if op.get("Name") == "_dd_trace_context":
            payload_str = op.get("StepDetails", {}).get("Result")
            if payload_str:
                try:
                    return json.loads(payload_str)
                except (json.JSONDecodeError, TypeError):
                    pass
            break

    return None


def _has_context_changed(before_headers: Optional[dict]) -> bool:
    """
    Component 3: Check if the current tracer context differs from the before headers.

    Only compares enrichable fields that customer code can modify:
    - x-datadog-tags (propagation tags like _dd.p.*)
    - x-datadog-sampling-priority
    - x-datadog-origin

    Does NOT compare trace_id, parent_id, span_id, traceparent, tracestate —
    these naturally change as new spans are created and are not "enrichment".
    """
    if not before_headers:
        return False

    current_headers = _inject_current_context()
    if not current_headers:
        return False

    # Fields that represent customer enrichment (not natural span lifecycle)
    enrichable_keys = ("x-datadog-tags", "x-datadog-sampling-priority", "x-datadog-origin")

    for key in enrichable_keys:
        before_val = before_headers.get(key)
        current_val = current_headers.get(key)
        if before_val != current_val:
            print(f"[DD-INTEGRATION] Trace context enrichment detected: {key} changed from '{before_val}' to '{current_val}'")
            return True

    return False


def save_updated_trace_checkpoint(state):
    """
    Component 3: Save updated trace context as a new checkpoint.

    Called from wrapper._after() when trace context has changed during the
    invocation (e.g., customer added baggage). The checkpoint contains the
    full propagation headers so the next invocation inherits the enriched context.
    """
    headers = _inject_current_context()
    if not headers:
        print("[DD-INTEGRATION] No active context for updated trace checkpoint")
        return

    trace_payload = json.dumps(headers, separators=(",", ":"))

    try:
        from aws_durable_execution_sdk_python.lambda_service import (
            OperationUpdate as SdkOperationUpdate,
            OperationType,
            OperationAction,
        )
        import hashlib
        import time

        execution_arn = getattr(state, "durable_execution_arn", "unknown")
        # Unique ID per invocation using timestamp
        checkpoint_id = hashlib.blake2b(
            f"_dd_updated_trace_ctx_{execution_arn}_{time.time()}".encode()
        ).hexdigest()[:64]

        start_update = SdkOperationUpdate(
            operation_id=checkpoint_id,
            operation_type=OperationType.STEP,
            action=OperationAction.START,
            name="_dd_trace_context",
        )
        state.create_checkpoint(start_update, is_sync=True)

        succeed_update = SdkOperationUpdate(
            operation_id=checkpoint_id,
            operation_type=OperationType.STEP,
            action=OperationAction.SUCCEED,
            name="_dd_trace_context",
            payload=trace_payload,
        )
        state.create_checkpoint(succeed_update, is_sync=True)

        print(f"[DD-INTEGRATION] Saved updated trace checkpoint with enriched context")
        log.debug(
            "[RequestID: %s] Saved updated trace checkpoint for execution %s",
            getattr(state, "_dd_aws_request_id", "unknown"),
            execution_arn,
        )
    except Exception as e:
        try:
            if hasattr(state, "_checkpointing_failed") and state._checkpointing_failed.is_set():
                state._checkpointing_failed.clear()
        except Exception:
            pass
        print(f"[DD-INTEGRATION] Failed to save updated trace checkpoint (non-fatal): {e}")
        log.debug("Failed to save updated trace checkpoint: %s", e)


@contextmanager
def _auto_trace_operation(
    operation_type: str,
    operation_id: str,
    state,
    attempt_number: Optional[int] = None,
    operation_name: Optional[str] = None,
):
    """
    Context manager that automatically traces a durable operation.

    Creates a span for the operation. The SDK only calls execute() for new
    operations — replayed operations return cached results without calling
    execute(), so no replay detection is needed here.

    The span is automatically set as the active span, making it available
    via tracer.current_span() for checkpoint injection.

    Args:
        operation_type: Type of operation (e.g., "step", "wait")
        operation_id: Unique identifier for this operation
        state: ExecutionState instance
        attempt_number: Attempt number for retries (optional)
        operation_name: Human-readable name for the operation (optional, used as span resource)
    """
    from ddtrace import tracer

    # Get request ID for logging
    request_id = getattr(state, "_dd_aws_request_id", "unknown")
    execution_arn = getattr(state, "durable_execution_arn", "unknown")

    print(f"[DD-INTEGRATION] _auto_trace_operation called:")
    print(f"  operation_type={operation_type}, operation_id={operation_id}")
    print(f"  operation_name={operation_name}")
    print(f"  attempt_number={attempt_number}")
    print(f"  execution_arn={execution_arn}")
    print(f"  request_id={request_id}")

    if not tracer.enabled:
        print(f"[DD-INTEGRATION] Tracer not enabled, skipping trace")
        yield None
        return

    # Use operation name as resource if available, otherwise fall back to operation_type
    resource_name = operation_name if operation_name else operation_type

    # Use DD_DURABLE_EXECUTION_SERVICE env var for service name, fall back to config default
    service_name = os.environ.get("DD_DURABLE_EXECUTION_SERVICE") or _INTEGRATION_CONFIG["_default_service"]

    # Create span - ddtrace automatically links it to the active parent span
    # (which is the Lambda wrapper span created by datadog-lambda-python)
    span = tracer.trace(
        f"aws.durable-execution.{operation_type}",
        service=service_name,
        resource=resource_name,
    )

    print(f"[DD-INTEGRATION] Created span: trace_id={span.trace_id}, span_id={span.span_id}, resource={resource_name}")

    # Set span tags
    span.set_tag("durable.operation_type", operation_type)
    span.set_tag("durable.operation_id", operation_id)
    if operation_name:
        span.set_tag("durable.operation_name", operation_name)
    span.set_tag("durable.execution_arn", execution_arn)
    span.set_tag("aws.request_id", request_id)

    # Add attempt number for retry tracking
    if attempt_number is not None:
        span.set_tag("durable.attempt", attempt_number)
        # Modify resource name to include attempt for retries
        if attempt_number > 1:
            span.resource = f"{resource_name} (attempt {attempt_number})"

    log.debug(
        "[RequestID: %s] Created span for %s operation: %s (resource=%s, trace_id=%s, span_id=%s)",
        request_id,
        operation_type,
        operation_id,
        resource_name,
        span.trace_id,
        span.span_id,
    )

    try:
        # Yield span - it's now the active span and available via tracer.current_span()
        yield span
    except Exception:
        # Caught an Exception (not BaseException) - this is a FINAL error
        # SuspendExecution and other control flow exceptions derive from BaseException,
        # so they pass through without being caught here
        exc_type, exc_val, exc_tb = sys.exc_info()
        span.set_exc_info(exc_type, exc_val, exc_tb)
        span.set_tag("durable.error.is_final", True)
        print(f"[DD-INTEGRATION] Final error captured: {exc_type.__name__}: {exc_val}")
        log.debug(
            "[RequestID: %s] Final error in %s operation %s: %s",
            request_id,
            operation_type,
            operation_id,
            exc_val,
        )
        raise
    finally:
        span.finish()
        print(f"[DD-INTEGRATION] Finished span for {resource_name}")
        log.debug(
            "[RequestID: %s] Finished span for %s operation: %s (resource=%s)",
            request_id,
            operation_type,
            operation_id,
            resource_name,
        )


def _patched_execution_state_init(wrapped, instance, args, kwargs):
    """
    Patch ExecutionState.__init__ to enable tracing.

    Stores request ID, sets global reference, and extracts "before" trace context
    from event payload for Component 3 change detection.
    """
    global _current_execution_state

    # Call original __init__
    result = wrapped(*args, **kwargs)

    # Store global reference so wrapper.py can create checkpoints
    _current_execution_state = instance

    # Extract Lambda context for request ID logging
    lambda_context = kwargs.get("lambda_context")
    instance._dd_aws_request_id = (
        getattr(lambda_context, "aws_request_id", "unknown")
        if lambda_context
        else "unknown"
    )

    # Store replay status for checkpoint logic
    is_replay = instance.is_replaying() if hasattr(instance, "is_replaying") else False
    instance._dd_is_replay = is_replay
    # Flag: need to save root span checkpoint on the first create_checkpoint call
    instance._dd_needs_root_checkpoint = not is_replay

    # Component 3: Extract "before" trace context from event payload.
    # This is the latest _dd_trace_context checkpoint from previous invocations.
    # Stored on the instance (not a global) so it's per-invocation.
    # For first invocation, this is None (set later after root checkpoint save).
    instance._dd_before_trace_headers = _extract_trace_headers_from_event_args(args)
    print(f"[DD-INTEGRATION] Before trace headers from event: {instance._dd_before_trace_headers is not None}")

    # LOGGING: Confirm patching is working
    print(f"[DD-INTEGRATION] ExecutionState.__init__ patched successfully")
    print(f"[DD-INTEGRATION] DurableExecutionArn: {instance.durable_execution_arn}")
    print(f"[DD-INTEGRATION] RequestID: {instance._dd_aws_request_id}")
    print(f"[DD-INTEGRATION] is_replay: {is_replay}")

    log.debug(
        "[RequestID: %s] ExecutionState initialized for tracing (replay=%s)",
        instance._dd_aws_request_id,
        is_replay,
    )

    return result


def _patched_create_checkpoint(wrapped, instance, args, kwargs):
    """
    Patch ExecutionState.create_checkpoint to piggyback trace context checkpoints.

    Customer checkpoint payloads are NEVER modified. Two piggyback behaviors:

    Component 2: On the first create_checkpoint call of the first invocation,
    save the root span's trace context as an extra checkpoint.

    Component 3: On every create_checkpoint call, compare the current tracer
    context against the "before" headers from the event payload (stored on
    instance._dd_before_trace_headers). If different (e.g., customer added
    baggage), save an updated checkpoint. This MUST happen during handler
    execution (when the batcher is active), NOT in wrapper._after() which
    runs after SuspendExecution when the batcher has shut down.
    """
    # create_checkpoint() can be called with no arguments (heartbeat/keep-alive).
    # In that case, just pass through.
    if not args and "operation_update" not in kwargs:
        return wrapped(*args, **kwargs)

    # Call original create_checkpoint FIRST — customer payload is never modified
    result = wrapped(*args, **kwargs)

    # Component 2: On first invocation, save root span checkpoint on the first
    # create_checkpoint call (piggyback on the first operation's checkpoint since
    # the batcher is now active). Uses _durable_root_span stored by wrapper.py.
    if getattr(instance, "_dd_needs_root_checkpoint", False):
        instance._dd_needs_root_checkpoint = False
        root_span = _durable_root_span
        if root_span:
            try:
                save_root_trace_checkpoint(instance, root_span)
            except Exception as e:
                print(f"[DD-INTEGRATION] Failed to save root checkpoint during first create_checkpoint: {e}")
        else:
            print("[DD-INTEGRATION] No root span stored on state, skipping root checkpoint")

    # Component 3: Compare current tracer context to "before" headers from event.
    # instance._dd_before_trace_headers is set in _patched_execution_state_init
    # (from event payload) or in save_root_trace_checkpoint (first invocation).
    # Re-entry guard: save_updated_trace_checkpoint calls create_checkpoint which
    # would trigger this patch again — skip if we're already saving.
    if getattr(instance, "_dd_saving_trace_checkpoint", False):
        return result
    before_headers = getattr(instance, "_dd_before_trace_headers", None)
    if before_headers is not None:
        try:
            if _has_context_changed(before_headers):
                instance._dd_saving_trace_checkpoint = True
                try:
                    save_updated_trace_checkpoint(instance)
                finally:
                    instance._dd_saving_trace_checkpoint = False
                # Update "before" so we don't save duplicate checkpoints
                current = _inject_current_context()
                if current:
                    instance._dd_before_trace_headers = current
        except Exception as e:
            print(f"[DD-INTEGRATION] Failed to save updated context checkpoint: {e}")

    return result


def _patched_step_execute(wrapped, instance, args, kwargs):
    """Patch StepOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] Step execution: operation_id={operation_id}, name={operation_name}, attempt={attempt_number}")

    with _auto_trace_operation(
        operation_type="step",
        operation_id=operation_id,
        state=state,
        attempt_number=attempt_number,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_wait_execute(wrapped, instance, args, kwargs):
    """Patch WaitOperationExecutor.execute to add automatic tracing."""
    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    print(f"[DD-INTEGRATION] Wait execution: operation_id={operation_id}, name={operation_name}")

    with _auto_trace_operation(
        operation_type="wait",
        operation_id=operation_id,
        state=state,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_invoke_execute(wrapped, instance, args, kwargs):
    """Patch InvokeOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] Invoke execution: operation_id={operation_id}, name={operation_name}")

    with _auto_trace_operation(
        operation_type="invoke",
        operation_id=operation_id,
        state=state,
        attempt_number=attempt_number,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_callback_execute(wrapped, instance, args, kwargs):
    """Patch CallbackOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] Callback execution: operation_id={operation_id}, name={operation_name}")

    with _auto_trace_operation(
        operation_type="callback",
        operation_id=operation_id,
        state=state,
        attempt_number=attempt_number,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_map_execute(wrapped, instance, args, kwargs):
    """Patch map_handler to add automatic tracing."""
    # Actual map_handler signature:
    #   map_handler(items, func, config, execution_state, map_context, operation_identifier)
    # Called from context.py with all keyword arguments.
    execution_state = kwargs.get("execution_state") or (args[3] if len(args) > 3 else None)
    operation_identifier = kwargs.get("operation_identifier") or (args[5] if len(args) > 5 else None)

    if not execution_state or not operation_identifier:
        print(f"[DD-INTEGRATION] Map handler: missing execution_state or operation_identifier, skipping tracing")
        return wrapped(*args, **kwargs)

    operation_id = operation_identifier.operation_id
    operation_name = getattr(operation_identifier, "name", None)

    print(f"[DD-INTEGRATION] Map execution: operation_id={operation_id}, name={operation_name}")

    with _auto_trace_operation(
        operation_type="map",
        operation_id=operation_id,
        state=execution_state,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_parallel_execute(wrapped, instance, args, kwargs):
    """Patch parallel_handler to add automatic tracing.

    parallel_handler signature:
        parallel_handler(callables, config, execution_state, parallel_context, operation_identifier)
    """
    execution_state = kwargs.get("execution_state") or (args[2] if len(args) > 2 else None)
    operation_identifier = kwargs.get("operation_identifier") or (args[4] if len(args) > 4 else None)

    if not execution_state or not operation_identifier:
        print(f"[DD-INTEGRATION] Parallel handler: missing execution_state or operation_identifier, skipping tracing")
        return wrapped(*args, **kwargs)

    operation_id = operation_identifier.operation_id
    operation_name = getattr(operation_identifier, "name", None)

    print(f"[DD-INTEGRATION] Parallel execution: operation_id={operation_id}, name={operation_name}")

    with _auto_trace_operation(
        operation_type="parallel",
        operation_id=operation_id,
        state=execution_state,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_child_context_execute(wrapped, instance, args, kwargs):
    """Patch ChildOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] ChildContext execution: operation_id={operation_id}, name={operation_name}")

    with _auto_trace_operation(
        operation_type="child_context",
        operation_id=operation_id,
        state=state,
        attempt_number=attempt_number,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_wait_for_condition_execute(wrapped, instance, args, kwargs):
    """Patch WaitForConditionOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] WaitForCondition execution: operation_id={operation_id}, name={operation_name}")

    with _auto_trace_operation(
        operation_type="wait_for_condition",
        operation_id=operation_id,
        state=state,
        attempt_number=attempt_number,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patch_operation_executor(operation_name, executor_class_name, patched_function):
    """
    Gracefully patch an operation executor if it exists.

    Args:
        operation_name: Name of the operation (e.g., "invoke", "map")
        executor_class_name: Executor class name (e.g., "InvokeOperationExecutor")
        patched_function: The patched execute function
    """
    try:
        module_path = f"aws_durable_execution_sdk_python.operation.{operation_name}"
        class_path = f"{module_path}.{executor_class_name}"

        _w(
            module_path,
            f"{executor_class_name}.execute",
            patched_function,
        )
        print(f"[DD-INTEGRATION] Patched {executor_class_name}")
        log.debug("Patched %s for tracing", executor_class_name)
    except (ImportError, AttributeError) as e:
        # Operation executor not available in this SDK version, skip gracefully
        log.debug("Could not patch %s: %s (operation may not be available)", executor_class_name, e)
        print(f"[DD-INTEGRATION] Skipped {executor_class_name} (not available in SDK)")


def patch():
    """Patch the AWS Durable Execution SDK for tracing."""
    try:
        import aws_durable_execution_sdk_python
        from aws_durable_execution_sdk_python.state import ExecutionState
        from aws_durable_execution_sdk_python.operation.step import StepOperationExecutor
        from aws_durable_execution_sdk_python.operation.wait import WaitOperationExecutor
    except ImportError:
        log.debug("aws_durable_execution_sdk_python not available for patching")
        return

    # Check if already patched
    if getattr(aws_durable_execution_sdk_python, "__datadog_patch", False):
        return

    # Patch ExecutionState.__init__ to add trace context storage
    _w(
        "aws_durable_execution_sdk_python.state",
        "ExecutionState.__init__",
        _patched_execution_state_init,
    )

    # Patch ExecutionState.create_checkpoint to inject trace context into payload
    _w(
        "aws_durable_execution_sdk_python.state",
        "ExecutionState.create_checkpoint",
        _patched_create_checkpoint,
    )

    # No deserialize patching needed — customer payloads are never modified.
    # Trace context is stored in separate checkpoints.

    # Patch StepOperationExecutor.execute for automatic tracing
    _w(
        "aws_durable_execution_sdk_python.operation.step",
        "StepOperationExecutor.execute",
        _patched_step_execute,
    )

    # Patch WaitOperationExecutor.execute for automatic tracing
    _w(
        "aws_durable_execution_sdk_python.operation.wait",
        "WaitOperationExecutor.execute",
        _patched_wait_execute,
    )

    # Patch additional operation executors (gracefully handle if not available)
    _patch_operation_executor("invoke", "InvokeOperationExecutor", _patched_invoke_execute)
    _patch_operation_executor("callback", "CallbackOperationExecutor", _patched_callback_execute)
    # Patch map_handler directly since it's a function, not a class method
    try:
        _w(
            "aws_durable_execution_sdk_python.operation.map",
            "map_handler",
            _patched_map_execute,
        )
        print("[DD-INTEGRATION] Patched map_handler")
        log.debug("Patched map_handler for tracing")
    except (ImportError, AttributeError) as e:
        log.debug("Could not patch map_handler: %s", e)
        print(f"[DD-INTEGRATION] Could not patch map_handler: {e}")
    # Patch parallel_handler directly since it's a function, not a class method (like map_handler)
    try:
        _w(
            "aws_durable_execution_sdk_python.operation.parallel",
            "parallel_handler",
            _patched_parallel_execute,
        )
        print("[DD-INTEGRATION] Patched parallel_handler")
        log.debug("Patched parallel_handler for tracing")
    except (ImportError, AttributeError) as e:
        log.debug("Could not patch parallel_handler: %s", e)
        print(f"[DD-INTEGRATION] Could not patch parallel_handler: {e}")
    # Patch ChildOperationExecutor.execute (class is ChildOperationExecutor, not ChildContextOperationExecutor)
    _patch_operation_executor("child", "ChildOperationExecutor", _patched_child_context_execute)
    _patch_operation_executor("wait_for_condition", "WaitForConditionOperationExecutor", _patched_wait_for_condition_execute)

    # Patch ThreadPoolExecutor in the SDK's concurrency module for trace context propagation
    # This ensures map/parallel operations don't create orphaned spans
    try:
        import aws_durable_execution_sdk_python.concurrency.executor as executor_module
        executor_module.ThreadPoolExecutor = TracedThreadPoolExecutor
        print("[DD-INTEGRATION] Patched ThreadPoolExecutor for trace context propagation")
        log.debug("Patched ThreadPoolExecutor in concurrency.executor module")
    except (ImportError, AttributeError) as e:
        log.debug("Could not patch ThreadPoolExecutor: %s", e)
        print(f"[DD-INTEGRATION] Could not patch ThreadPoolExecutor: {e}")

    aws_durable_execution_sdk_python.__datadog_patch = True
    log.info("Patched aws_durable_execution_sdk_python for tracing")
    print("[DD-INTEGRATION] Successfully patched AWS Durable Execution SDK")


def unpatch():
    """Unpatch the AWS Durable Execution SDK."""
    try:
        import aws_durable_execution_sdk_python
    except ImportError:
        return

    if not getattr(aws_durable_execution_sdk_python, "__datadog_patch", False):
        return

    # Restore original ThreadPoolExecutor in the SDK's concurrency module
    try:
        import aws_durable_execution_sdk_python.concurrency.executor as executor_module
        executor_module.ThreadPoolExecutor = _OriginalThreadPoolExecutor
        log.debug("Restored original ThreadPoolExecutor in concurrency.executor module")
    except (ImportError, AttributeError) as e:
        log.debug("Could not restore ThreadPoolExecutor: %s", e)

    # Note: wrapt doesn't provide an easy way to unpatch the other patches
    # This would require storing original functions and restoring them
    aws_durable_execution_sdk_python.__datadog_patch = False
    log.info("Unpatched aws_durable_execution_sdk_python")
