"""
Instrumentation for AWS Durable Execution SDK.

Traces durable operations (step, wait, invoke, etc.) and propagates trace context
across Lambda suspensions and replays by storing context in AWS checkpoints.
"""

import contextvars
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Dict, Optional

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import ERROR_MSG, ERROR_STACK, ERROR_TYPE
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from wrapt import wrap_function_wrapper as _w


log = get_logger(__name__)


# Module-level configuration
_INTEGRATION_CONFIG = {
    "_default_service": "aws.durable.execution",
    "distributed_tracing": True,
    "trace_replays": False,  # Don't create spans for replayed operations
}

config._add("aws_durable_execution_sdk_python", _INTEGRATION_CONFIG)


# Store reference to original ThreadPoolExecutor for unpatching
_OriginalThreadPoolExecutor = ThreadPoolExecutor


class TracedThreadPoolExecutor(ThreadPoolExecutor):
    """
    ThreadPoolExecutor that propagates trace context to worker threads.

    This ensures that spans created in worker threads (e.g., for map/parallel operations)
    are connected to the parent trace instead of being orphaned.

    The context is captured at submit time and restored when the task executes,
    so tracer.current_span() returns the correct parent span in worker threads.
    """

    def submit(self, fn, /, *args, **kwargs):
        """Submit a task with trace context propagation."""
        # Capture current context (includes ddtrace's trace context via contextvars)
        ctx = contextvars.copy_context()

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


def _inject_trace_context_into_payload(payload_json: str, trace_context: Dict[str, Any]) -> str:
    """
    Inject trace context into an OperationUpdate payload.

    The payload is a JSON string. We parse it, add trace context, and re-serialize.
    Uses __dd_instrumentation__ namespace to avoid collisions with customer data.

    Args:
        payload_json: JSON string payload from OperationUpdate
        trace_context: Dict with trace_id, span_id, sampling_priority

    Returns:
        Modified JSON string with trace context injected
    """
    if not payload_json or not trace_context:
        return payload_json

    try:
        # Parse existing payload
        payload_data = json.loads(payload_json)

        # Wrap in dict if not already a dict
        if not isinstance(payload_data, dict):
            payload_data = {"_value": payload_data}

        # Use namespaced key to avoid collisions
        # "__dd_instrumentation__" is very unlikely to collide with customer code
        instrumentation_key = "__dd_instrumentation__"

        # Check if key already exists (extremely unlikely, but be safe)
        if instrumentation_key in payload_data:
            log.warning(
                "Payload already contains '%s' key - customer data may be using reserved namespace. "
                "Overwriting with trace context.",
                instrumentation_key
            )

        # Inject trace context in namespaced location
        payload_data[instrumentation_key] = {
            "trace_context": trace_context,
            "version": "1.0"  # Version for future compatibility
        }

        # Re-serialize
        return json.dumps(payload_data, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError) as e:
        log.warning("Failed to inject trace context into payload: %s", e)
        return payload_json


def _extract_trace_context_from_payload(payload_json: str) -> Optional[Dict[str, Any]]:
    """
    Extract trace context from an OperationUpdate payload.

    Args:
        payload_json: JSON string payload from OperationUpdate

    Returns:
        Dict with trace_id, span_id, sampling_priority, or None
    """
    if not payload_json:
        return None

    try:
        payload_data = json.loads(payload_json)
        if isinstance(payload_data, dict):
            # Try new namespaced format first (v1.0+)
            instrumentation_data = payload_data.get("__dd_instrumentation__")
            if instrumentation_data and isinstance(instrumentation_data, dict):
                return instrumentation_data.get("trace_context")

            # Fall back to old format for backward compatibility
            # (in case we're reading checkpoints created before this change)
            legacy_trace_context = payload_data.get("_dd_trace_context")
            if legacy_trace_context:
                log.debug("Found trace context in legacy format (_dd_trace_context)")
                return legacy_trace_context

        return None
    except (json.JSONDecodeError, TypeError) as e:
        log.warning("Failed to extract trace context from payload: %s", e)
        return None


@contextmanager
def _auto_trace_operation(
    operation_type: str,
    operation_id: str,
    state,
    is_replaying: bool = False,
    attempt_number: Optional[int] = None,
    operation_name: Optional[str] = None,
):
    """
    Context manager that automatically traces a durable operation.

    Creates a span for the operation if:
    - Not replaying (or trace_replays is enabled)
    - Tracer is available

    The span is automatically set as the active span, making it available
    via tracer.current_span() for checkpoint injection.

    Args:
        operation_type: Type of operation (e.g., "step", "wait")
        operation_id: Unique identifier for this operation
        state: ExecutionState instance
        is_replaying: Whether this operation is being replayed from cache
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
    print(f"  is_replaying={is_replaying}, attempt_number={attempt_number}")
    print(f"  execution_arn={execution_arn}")
    print(f"  request_id={request_id}")

    pin = Pin.get_from(state)
    if not pin or not pin.enabled():
        print(f"[DD-INTEGRATION] Pin not enabled, skipping trace")
        yield None
        return

    # Check if we should trace this operation
    should_trace = not is_replaying or _INTEGRATION_CONFIG["trace_replays"]
    print(f"[DD-INTEGRATION] should_trace={should_trace} (is_replaying={is_replaying}, trace_replays={_INTEGRATION_CONFIG['trace_replays']})")

    span = None

    if should_trace:
        # Use operation name as resource if available, otherwise fall back to operation_id
        resource_name = operation_name if operation_name else operation_id

        # Create span - ddtrace automatically links it to the active parent span
        # (which is the Lambda wrapper span created by datadog-lambda-python)
        span = tracer.trace(
            f"aws.durable.{operation_type}",
            service=_INTEGRATION_CONFIG["_default_service"],
            resource=resource_name,
        )

        print(f"[DD-INTEGRATION] Created span: trace_id={span.trace_id}, span_id={span.span_id}, resource={resource_name}")

        # Set span tags
        span.set_tag("durable.operation_type", operation_type)
        span.set_tag("durable.operation_id", operation_id)
        if operation_name:
            span.set_tag("durable.operation_name", operation_name)
        span.set_tag("durable.execution_arn", execution_arn)
        span.set_tag("durable.is_replaying", is_replaying)
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
    else:
        print(f"[DD-INTEGRATION] Skipping span creation (replaying)")

    try:
        # Yield span - it's now the active span and available via tracer.current_span()
        yield span
    except Exception:
        # Caught an Exception (not BaseException) - this is a FINAL error
        # SuspendExecution and other control flow exceptions derive from BaseException,
        # so they pass through without being caught here
        if span:
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
        # Finish span
        if span:
            span.finish()
            resource_name_for_log = operation_name if operation_name else operation_id
            print(f"[DD-INTEGRATION] Finished span for {resource_name_for_log}")
            log.debug(
                "[RequestID: %s] Finished span for %s operation: %s (resource=%s)",
                request_id,
                operation_type,
                operation_id,
                resource_name_for_log,
            )


def _patched_execution_state_init(wrapped, instance, args, kwargs):
    """
    Patch ExecutionState.__init__ to enable tracing.

    Adds a Pin for tracing and stores request ID for logging.
    No in-memory cache needed - trace context comes from active spans.
    """
    # Call original __init__
    result = wrapped(*args, **kwargs)

    # Extract Lambda context for request ID logging
    lambda_context = kwargs.get("lambda_context")
    instance._dd_aws_request_id = (
        getattr(lambda_context, "aws_request_id", "unknown")
        if lambda_context
        else "unknown"
    )

    # Pin for tracing
    Pin().onto(instance)

    # LOGGING: Confirm patching is working
    print(f"[DD-INTEGRATION] ExecutionState.__init__ patched successfully")
    print(f"[DD-INTEGRATION] DurableExecutionArn: {instance.durable_execution_arn}")
    print(f"[DD-INTEGRATION] RequestID: {instance._dd_aws_request_id}")
    print(f"[DD-INTEGRATION] Initial operations count: {len(instance.operations)}")

    log.debug(
        "[RequestID: %s] ExecutionState initialized for tracing",
        instance._dd_aws_request_id,
    )

    return result


def _patched_create_checkpoint(wrapped, instance, args, kwargs):
    """
    Patch ExecutionState.create_checkpoint to inject trace context into OperationUpdate.

    Reads trace context from the currently active span (tracer.current_span())
    and injects it into the checkpoint payload before sending to AWS.
    """
    from ddtrace import tracer

    # Get the operation_update argument (first positional or keyword arg)
    operation_update = get_argument_value(args, kwargs, 0, "operation_update")

    if operation_update is not None:
        operation_id = operation_update.operation_id

        print(f"[DD-INTEGRATION] Intercepting checkpoint for operation: {operation_id}")

        # Get trace context from the currently active span
        active_span = tracer.current_span()

        if active_span and hasattr(operation_update, "payload") and operation_update.payload:
            # Get sampling priority - CRITICAL for ensuring consistent sampling across invocations
            # The sampling_priority determines whether this trace is kept or dropped by the backend
            sampling_priority = None
            if active_span.context and hasattr(active_span.context, "sampling_priority"):
                sampling_priority = active_span.context.sampling_priority
            # If no sampling priority set, default to AUTO_KEEP (1) to ensure trace is kept
            if sampling_priority is None:
                sampling_priority = 1

            trace_context = {
                "trace_id": active_span.trace_id,
                "span_id": active_span.span_id,
                "sampling_priority": sampling_priority,
            }

            print(f"[DD-INTEGRATION] Retrieved trace context from active span: {trace_context}")
            print(f"[DD-INTEGRATION] Injecting trace context into checkpoint payload")
            print(f"[DD-INTEGRATION] Original payload length: {len(operation_update.payload)}")

            # Inject trace context into payload
            modified_payload = _inject_trace_context_into_payload(
                operation_update.payload,
                trace_context
            )

            # Modify the OperationUpdate (this is a frozen dataclass, so we need to use object.__setattr__)
            object.__setattr__(operation_update, "payload", modified_payload)

            print(f"[DD-INTEGRATION] Modified payload length: {len(modified_payload)}")
            print(f"[DD-INTEGRATION] Trace context injected successfully")

            log.debug(
                "[RequestID: %s] Injected trace context from active span into checkpoint for operation: %s",
                getattr(instance, "_dd_aws_request_id", "unknown"),
                operation_id,
            )
        else:
            if not active_span:
                print(f"[DD-INTEGRATION] No active span found for operation: {operation_id}")
            if not hasattr(operation_update, "payload"):
                print(f"[DD-INTEGRATION] OperationUpdate has no payload attribute")
            elif not operation_update.payload:
                print(f"[DD-INTEGRATION] OperationUpdate payload is empty")

    # Call original create_checkpoint
    return wrapped(*args, **kwargs)


def _patched_step_execute(wrapped, instance, args, kwargs):
    """Patch StepOperationExecutor.execute to add automatic tracing."""
    # Get the checkpointed_result argument
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")

    # Detect if replaying (operation already succeeded)
    is_replaying = checkpointed_result.is_succeeded() if checkpointed_result else False

    # Get operation info
    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    # Get attempt number for retry tracking
    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] Step execution: operation_id={operation_id}, name={operation_name}, is_replaying={is_replaying}, attempt={attempt_number}")

    # Wrap execution with auto-tracing
    with _auto_trace_operation(
        operation_type="step",
        operation_id=operation_id,
        state=state,
        is_replaying=is_replaying,
        attempt_number=attempt_number,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_wait_execute(wrapped, instance, args, kwargs):
    """Patch WaitOperationExecutor.execute to add automatic tracing."""
    # Wait operations are only called when actually waiting (never during replay)
    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    print(f"[DD-INTEGRATION] Wait execution: operation_id={operation_id}, name={operation_name}")

    # Wrap execution with auto-tracing
    with _auto_trace_operation(
        operation_type="wait",
        operation_id=operation_id,
        state=state,
        is_replaying=False,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_invoke_execute(wrapped, instance, args, kwargs):
    """Patch InvokeOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")
    is_replaying = checkpointed_result.is_succeeded() if checkpointed_result else False

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] Invoke execution: operation_id={operation_id}, name={operation_name}, is_replaying={is_replaying}")

    with _auto_trace_operation(
        operation_type="invoke",
        operation_id=operation_id,
        state=state,
        is_replaying=is_replaying,
        attempt_number=attempt_number,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_callback_execute(wrapped, instance, args, kwargs):
    """Patch CallbackOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")
    is_replaying = checkpointed_result.is_succeeded() if checkpointed_result else False

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] Callback execution: operation_id={operation_id}, name={operation_name}, is_replaying={is_replaying}")

    with _auto_trace_operation(
        operation_type="callback",
        operation_id=operation_id,
        state=state,
        is_replaying=is_replaying,
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

    # map_handler internally checks checkpoint - get it for replay detection
    checkpoint = execution_state.get_checkpoint_result(operation_id)
    is_replaying = checkpoint.is_succeeded() if checkpoint else False

    print(f"[DD-INTEGRATION] Map execution: operation_id={operation_id}, name={operation_name}, is_replaying={is_replaying}")

    with _auto_trace_operation(
        operation_type="map",
        operation_id=operation_id,
        state=execution_state,
        is_replaying=is_replaying,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_parallel_execute(wrapped, instance, args, kwargs):
    """Patch ParallelOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")
    is_replaying = checkpointed_result.is_succeeded() if checkpointed_result else False

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] Parallel execution: operation_id={operation_id}, name={operation_name}, is_replaying={is_replaying}")

    with _auto_trace_operation(
        operation_type="parallel",
        operation_id=operation_id,
        state=state,
        is_replaying=is_replaying,
        attempt_number=attempt_number,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_child_context_execute(wrapped, instance, args, kwargs):
    """Patch ChildContextOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")
    is_replaying = checkpointed_result.is_succeeded() if checkpointed_result else False

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] ChildContext execution: operation_id={operation_id}, name={operation_name}, is_replaying={is_replaying}")

    with _auto_trace_operation(
        operation_type="child_context",
        operation_id=operation_id,
        state=state,
        is_replaying=is_replaying,
        attempt_number=attempt_number,
        operation_name=operation_name,
    ):
        return wrapped(*args, **kwargs)


def _patched_wait_for_condition_execute(wrapped, instance, args, kwargs):
    """Patch WaitForConditionOperationExecutor.execute to add automatic tracing."""
    checkpointed_result = get_argument_value(args, kwargs, 0, "checkpointed_result")
    is_replaying = checkpointed_result.is_succeeded() if checkpointed_result else False

    operation_id = instance.operation_identifier.operation_id
    operation_name = getattr(instance.operation_identifier, "name", None)
    state = instance.state

    attempt_number = None
    if checkpointed_result and hasattr(checkpointed_result, "attempt"):
        attempt_number = checkpointed_result.attempt

    print(f"[DD-INTEGRATION] WaitForCondition execution: operation_id={operation_id}, name={operation_name}, is_replaying={is_replaying}")

    with _auto_trace_operation(
        operation_type="wait_for_condition",
        operation_id=operation_id,
        state=state,
        is_replaying=is_replaying,
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
    _patch_operation_executor("parallel", "ParallelOperationExecutor", _patched_parallel_execute)
    _patch_operation_executor("child_context", "ChildContextOperationExecutor", _patched_child_context_execute)
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
