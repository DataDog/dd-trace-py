"""
Instrumentation for the aws-durable-execution-sdk-python library.

Wraps the following targets:
  - ``durable_execution`` decorator (workflow execution spans)
  - ``DurableContext.step`` (step execution spans)
  - ``DurableContext.invoke`` (cross-function invocation spans)
  - ``DurableContext.wait`` (wait spans)
  - ``DurableContext.wait_for_condition`` (wait-for-condition spans)
  - ``DurableContext.wait_for_callback`` (wait-for-callback spans)
  - ``DurableContext.create_callback`` (create-callback spans)
  - ``DurableContext.map`` (map spans)
  - ``DurableContext.parallel`` (parallel spans)
  - ``DurableContext.run_in_child_context`` (child-context spans)

Also patches the SDK's internal ThreadPoolExecutor to propagate trace
context into worker threads used by map/parallel operations.

AIDEV-NOTE: SuspendExecution is a BaseException used for control flow.
It must NOT mark spans as errored. All wrappers explicitly handle it.
"""
import contextvars
import functools
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import aws_durable_execution_sdk_python

from ddtrace import config
from ddtrace._trace.events import TracingEvent
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator as Propagator


log = get_logger(__name__)

_CHECKPOINT_NAME_PREFIX = "_dd_trace_context_"
_TERMINAL_STATUSES = ("SUCCEEDED", "FAILED")


config._add(
    "aws_durable_execution_sdk_python",
    dict(
        _default_service="aws.durable_execution",
        distributed_tracing_enabled=True,
    ),
)


def get_version() -> str:
    return getattr(aws_durable_execution_sdk_python, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aws_durable_execution_sdk_python": ">=1.4.0"}


# ---------------------------------------------------------------------------
# Service-name helper
# ---------------------------------------------------------------------------

def _get_service():
    """Return the service name for all durable-function spans.

    Checks ``DD_DURABLE_EXECUTION_SERVICE`` first (short alias shared with
    the Node.js integration), then falls back to the standard dd-trace-py
    resolution chain (``DD_AWS_DURABLE_EXECUTION_SDK_PYTHON_SERVICE`` →
    ``DD_SERVICE`` → config default).
    """
    custom = os.environ.get("DD_DURABLE_EXECUTION_SERVICE")
    if custom:
        return custom
    return int_service(None, config.aws_durable_execution_sdk_python)


# ---------------------------------------------------------------------------
# TracedThreadPoolExecutor — trace-context propagation for map/parallel
# ---------------------------------------------------------------------------

_OriginalThreadPoolExecutor = ThreadPoolExecutor


class TracedThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that propagates trace context to worker threads.

    Captures context at init time as a fallback for re-submissions from
    background threads (e.g., SDK's TimerScheduler retries) that have no
    trace context.  At submit time, if the current thread has an active
    trace context it is used; otherwise the init-time context is used.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_ctx = contextvars.copy_context()

    def submit(self, fn, /, *args, **kwargs):
        from ddtrace import tracer

        active = tracer.context_provider.active()
        if active is not None:
            ctx = contextvars.copy_context()
        else:
            # Create a fresh copy — Context.run() is not reentrant so we
            # cannot share self._init_ctx across concurrent threads.
            ctx = self._init_ctx.run(contextvars.copy_context)

        def _wrapped(*a, **kw):
            return ctx.run(fn, *a, **kw)

        return super().submit(_wrapped, *args, **kwargs)


# ---------------------------------------------------------------------------
# Trace-context checkpoint helpers
# ---------------------------------------------------------------------------
# AIDEV-NOTE: Trace context is persisted across Lambda invocations by writing
# it into the durable execution state as a STEP operation named
# ``_dd_trace_context_{N}``.  The datadog-lambda-python wrapper reads it back
# on the next invocation and activates the context before creating aws.lambda.
#
# parent_id semantics: the checkpoint's parent_id should point to the root
# ``aws.durable-execution`` span (the grandparent of the
# ``aws.durable_execution.execute`` span being closed).  This keeps each
# invocation's ``aws.lambda`` span as a sibling of previous invocations,
# not a descendant.
# ---------------------------------------------------------------------------


def _find_trace_context_checkpoints(state):
    """Return sorted list of (number, operation) for existing checkpoints."""
    found = []
    operations = getattr(state, "operations", None) or {}
    for op in operations.values():
        name = getattr(op, "name", None)
        if not name or not name.startswith(_CHECKPOINT_NAME_PREFIX):
            continue
        suffix = name[len(_CHECKPOINT_NAME_PREFIX):]
        try:
            number = int(suffix)
        except ValueError:
            continue
        found.append((number, op))
    found.sort(key=lambda t: t[0])
    return found


def _parse_checkpoint_payload(op):
    """Extract the headers dict from a checkpoint STEP operation's result."""
    try:
        step_details = getattr(op, "step_details", None)
        if step_details is None:
            return None
        result = getattr(step_details, "result", None)
        if not result:
            return None
        parsed = json.loads(result)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _headers_trace_id(headers):
    """Extract trace_id from headers (Datadog or W3C format)."""
    tid = headers.get("x-datadog-trace-id")
    if tid is not None:
        return str(tid)
    tp = headers.get("traceparent")
    if isinstance(tp, str):
        parts = tp.split("-")
        if len(parts) == 4:
            return parts[1]
    return None


def _headers_parent_id(headers):
    """Extract parent_id (as string) from headers (Datadog or W3C format)."""
    pid = headers.get("x-datadog-parent-id")
    if pid is not None:
        return str(pid)
    tp = headers.get("traceparent")
    if isinstance(tp, str):
        parts = tp.split("-")
        if len(parts) == 4:
            return parts[2]
    return None


def _normalize_headers_ignoring_parent_id(headers):
    """Return a copy of headers with the parent_id fields zeroed/removed."""
    result = dict(headers)
    result.pop("x-datadog-parent-id", None)
    tp = result.get("traceparent")
    if isinstance(tp, str):
        parts = tp.split("-")
        if len(parts) == 4:
            parts[2] = "0" * 16
            result["traceparent"] = "-".join(parts)
    return result


def _override_parent_id(headers, parent_id):
    """Set parent_id on headers in-place (both Datadog and W3C formats)."""
    if parent_id is None:
        return
    if "x-datadog-trace-id" in headers:
        headers["x-datadog-parent-id"] = str(parent_id)
    tp = headers.get("traceparent")
    if isinstance(tp, str):
        parts = tp.split("-")
        if len(parts) == 4:
            try:
                as_int = int(parent_id)
                parts[2] = format(as_int, "016x")
            except (TypeError, ValueError):
                # If parent_id is already hex, use as-is (padded/truncated)
                parts[2] = str(parent_id).rjust(16, "0")[:16]
            headers["traceparent"] = "-".join(parts)


def _save_trace_context_checkpoint(state, number, headers):
    """Create a ``_dd_trace_context_{number}`` STEP operation with the payload."""
    from aws_durable_execution_sdk_python.identifier import OperationIdentifier
    from aws_durable_execution_sdk_python.lambda_service import OperationUpdate

    name = "{}{}".format(_CHECKPOINT_NAME_PREFIX, number)
    arn = getattr(state, "durable_execution_arn", "") or ""
    op_id = hashlib.blake2b("{}:{}".format(name, arn).encode()).hexdigest()[:64]
    payload = json.dumps(headers)

    identifier = OperationIdentifier(operation_id=op_id, name=name)

    state.create_checkpoint(
        operation_update=OperationUpdate.create_step_start(identifier),
        is_sync=True,
    )
    state.create_checkpoint(
        operation_update=OperationUpdate.create_step_succeed(identifier, payload),
        is_sync=True,
    )
    log.debug("Saved trace context checkpoint %s", name)


def _maybe_save_trace_context_checkpoint(span, durable_context, grandparent_span_id, result):
    """Save a trace-context checkpoint if the execution will continue and context changed.

    Called right before the ``aws.durable_execution.execute`` span closes.
    Skips saving when:
      - There's no DurableContext / ExecutionState
      - The response ``Status`` is ``SUCCEEDED`` or ``FAILED`` (terminal)
      - The current trace context matches the latest existing checkpoint
        (ignoring parent_id)
    """
    if span is None or durable_context is None:
        return
    state = getattr(durable_context, "state", None)
    if state is None:
        return

    # Skip for terminal statuses — no next invocation
    if isinstance(result, dict):
        status = result.get("Status")
        if status in _TERMINAL_STATUSES:
            return

    # Inject current trace context into headers
    try:
        current_headers = {}
        Propagator.inject(span.context, current_headers)
    except Exception:
        return
    if not current_headers:
        return

    existing = _find_trace_context_checkpoints(state)

    if not existing:
        # First checkpoint: _dd_trace_context_0, parent_id = grandparent (root)
        new_number = 0
        if grandparent_span_id is not None:
            _override_parent_id(current_headers, grandparent_span_id)
    else:
        latest_number, latest_op = existing[-1]
        latest_headers = _parse_checkpoint_payload(latest_op)
        if latest_headers is None:
            return

        # Warn if trace_id differs — should never happen in a single execution
        cur_tid = _headers_trace_id(current_headers)
        prev_tid = _headers_trace_id(latest_headers)
        if cur_tid and prev_tid and cur_tid != prev_tid:
            log.warning(
                "Trace ID mismatch between checkpoints: current=%s previous=%s",
                cur_tid,
                prev_tid,
            )

        # Skip save if trace context is unchanged (ignoring parent_id)
        if _normalize_headers_ignoring_parent_id(current_headers) == \
                _normalize_headers_ignoring_parent_id(latest_headers):
            return

        new_number = latest_number + 1
        # Reuse previous checkpoint's parent_id (same grandparent)
        _override_parent_id(current_headers, _headers_parent_id(latest_headers))

    try:
        _save_trace_context_checkpoint(state, new_number, current_headers)
    except Exception as e:
        log.debug("Failed to save trace context checkpoint: %s", e)


# ---------------------------------------------------------------------------
# Wrapper: durable_execution decorator
# ---------------------------------------------------------------------------
# AIDEV-NOTE: durable_execution is a decorator. Wrapping it intercepts the
# decoration call.  We replace the user function with a traced version so
# the execution span lives in the same thread as step/invoke spans, giving
# correct parent-child relationships without needing cross-thread context
# propagation.
# ---------------------------------------------------------------------------
def _traced_durable_execution(func, instance, args, kwargs):
    """Wrapper for the durable_execution decorator function.

    When ``@durable_execution`` is applied to a user function, this wrapper
    intercepts the call, replaces the user function with a traced version,
    and delegates to the original decorator.
    """
    # When called with keyword-only args (e.g. @durable_execution(boto3_client=...)),
    # func receives None and returns a functools.partial. Let it pass through;
    # the partial will call us again with the actual user function.
    user_func = args[0] if args else kwargs.get("func")

    if user_func is None or not callable(user_func):
        return func(*args, **kwargs)

    @functools.wraps(user_func)
    def traced_user_func(*inner_args, **inner_kwargs):
        """Traced wrapper around the user's workflow function."""
        from aws_durable_execution_sdk_python.exceptions import SuspendExecution

        # AIDEV-NOTE: The user function signature is (input_event, durable_context).
        input_event = inner_args[0] if inner_args else inner_kwargs.get("input_event")
        durable_context = inner_args[1] if len(inner_args) > 1 else inner_kwargs.get("durable_context")

        # --- Extract metadata from the DurableContext ---
        tags = {}
        try:
            arn = getattr(durable_context.state, "durable_execution_arn", None) if durable_context else None
            if arn:
                tags["aws.durable_execution.arn"] = arn
        except Exception:
            pass
        try:
            if durable_context:
                status_enum = getattr(durable_context.state, "_replay_status", None)
                if status_enum is not None:
                    tags["aws.durable_execution.replay_status"] = status_enum.name
        except Exception:
            pass

        # --- Extract distributed tracing context from input_event ---
        # AIDEV-NOTE: When a durable execution is invoked via DurableContext.invoke(),
        # the caller injects trace context into the payload dict under a "_datadog" key.
        distributed_context = None
        if config.aws_durable_execution_sdk_python.distributed_tracing_enabled:
            if isinstance(input_event, dict) and "_datadog" in input_event:
                try:
                    parent_ctx = Propagator.extract(input_event["_datadog"])
                    if parent_ctx.trace_id is not None:
                        distributed_context = parent_ctx
                except Exception:
                    pass

        # --- Snapshot grandparent span_id BEFORE creating the execution span ---
        # The active span at this point is aws.lambda (created by datadog-lambda-python).
        # Its parent_id is the root aws.durable-execution span's span_id, which we
        # want to use as the parent_id in trace-context checkpoints so that next
        # invocations' aws.lambda spans parent to the root rather than nesting.
        grandparent_span_id = None
        try:
            from ddtrace import tracer as _tracer
            lambda_span = _tracer.current_span()
            if lambda_span is not None:
                grandparent_span_id = lambda_span.parent_id
        except Exception:
            pass

        # --- Create the execution span via event ---
        event = TracingEvent.create(
            component=config.aws_durable_execution_sdk_python.integration_name,
            integration_config=config.aws_durable_execution_sdk_python,
            operation_name="aws.durable_execution.execute",
            span_type="serverless",
            span_kind="server",
            resource="aws.durable_execution.execute",
            service=_get_service(),
            tags=tags,
            distributed_context=distributed_context,
            use_active_context=distributed_context is None,
        )

        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            try:
                result = user_func(*inner_args, **inner_kwargs)
            except SuspendExecution:
                # AIDEV-NOTE: SuspendExecution is control flow, not an error.
                # Save checkpoint before closing the span so the span is still
                # active (propagator.inject reads from ctx.span.context).
                _maybe_save_trace_context_checkpoint(
                    ctx.span, durable_context, grandparent_span_id, None
                )
                ctx.dispatch_ended_event()
                raise
            except BaseException:
                exc_info = sys.exc_info()
                _maybe_save_trace_context_checkpoint(
                    ctx.span, durable_context, grandparent_span_id, None
                )
                ctx.dispatch_ended_event(*exc_info)
                raise
            _maybe_save_trace_context_checkpoint(
                ctx.span, durable_context, grandparent_span_id, result
            )
            ctx.dispatch_ended_event()
            return result

    # Replace user func with traced version, call the original decorator
    new_args = (traced_user_func,) + args[1:]
    return func(*new_args, **kwargs)


# ---------------------------------------------------------------------------
# Wrapper: DurableContext.step
# ---------------------------------------------------------------------------

def _try_tag_replayed(span, step_func, step_executed):
    """Tag a step span with whether it was replayed from checkpoint.

    AIDEV-NOTE: When the SDK replays a step from checkpoint, it returns the cached
    result without calling the step function.  The ``step_executed`` flag tracks
    whether the function was actually invoked, giving accurate per-step replay
    detection without relying on private SDK internals.
    """
    try:
        if callable(step_func):
            span.set_tag("aws.durable_execution.step.replayed", str(not step_executed[0]).lower())
    except Exception:
        pass


def _traced_step(func, instance, args, kwargs):
    """Wrapper for DurableContext.step().

    Creates a child span for each step execution with the step name
    and standard tags.
    """
    from aws_durable_execution_sdk_python.exceptions import SuspendExecution

    # --- Extract step name ---
    step_func = args[0] if args else None
    name_kwarg = kwargs.get("name")
    step_name = (
        name_kwarg
        or getattr(step_func, "_original_name", None)
        or getattr(step_func, "__name__", None)
    )

    # --- Wrap step function to detect replay vs fresh execution ---
    # AIDEV-NOTE: When the SDK replays a step from checkpoint, it returns the cached
    # result without calling the step function.  By wrapping the function and tracking
    # whether it was invoked, we accurately determine if the step was replayed.
    _step_executed = [False]
    if callable(step_func):
        _original_step_func = step_func

        @functools.wraps(_original_step_func)
        def _tracked_step_func(*a, **kw):
            _step_executed[0] = True
            return _original_step_func(*a, **kw)

        args = (_tracked_step_func,) + args[1:]

    # --- Build tags ---
    tags = {}
    if step_name:
        tags["aws.durable_execution.step.name"] = step_name

    # --- Create the step span via event ---
    event = TracingEvent.create(
        component=config.aws_durable_execution_sdk_python.integration_name,
        integration_config=config.aws_durable_execution_sdk_python,
        operation_name="aws.durable_execution.step",
        span_type="worker",
        span_kind="internal",
        resource=step_name or "step",
        service=_get_service(),
        tags=tags,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            result = func(*args, **kwargs)
        except SuspendExecution:
            _try_tag_replayed(ctx.span, step_func, _step_executed)
            ctx.dispatch_ended_event()
            raise
        except BaseException:
            _try_tag_replayed(ctx.span, step_func, _step_executed)
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        _try_tag_replayed(ctx.span, step_func, _step_executed)
        ctx.dispatch_ended_event()
        return result


# ---------------------------------------------------------------------------
# Wrapper: DurableContext.invoke
# ---------------------------------------------------------------------------

def _traced_invoke(func, instance, args, kwargs):
    """Wrapper for DurableContext.invoke().

    Creates a client span for cross-function invocations with the target
    function name and invocation name.
    """
    from aws_durable_execution_sdk_python.exceptions import SuspendExecution

    # --- Extract invoke parameters ---
    # invoke(function_name, payload, name=None, config=None)
    function_name = kwargs.get("function_name") or (args[0] if args else None)
    invoke_name = kwargs.get("name")

    # --- Build tags ---
    tags = {}
    if function_name:
        tags["aws.durable_execution.invoke.function_name"] = str(function_name)
        # AIDEV-NOTE: out.host enables peer service computation by the
        # PeerServiceProcessor.  It derives peer.service from out.host on
        # client spans, giving downstream dependency visibility in APM.
        tags["out.host"] = str(function_name)
    if invoke_name:
        tags["aws.durable_execution.invoke.name"] = invoke_name

    # --- Create the invoke span via event ---
    event = TracingEvent.create(
        component=config.aws_durable_execution_sdk_python.integration_name,
        integration_config=config.aws_durable_execution_sdk_python,
        operation_name="aws.durable_execution.invoke",
        span_type="serverless",
        span_kind="client",
        resource=invoke_name or function_name or "invoke",
        service=_get_service(),
        tags=tags,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        # --- Inject distributed tracing context into payload ---
        # AIDEV-NOTE: When the payload is a dict, we inject trace context under a
        # "_datadog" key so the downstream @durable_execution handler can extract it
        # and link its execution span to this invoke span's trace.
        if config.aws_durable_execution_sdk_python.distributed_tracing_enabled:
            try:
                payload = kwargs.get("payload") or (args[1] if len(args) > 1 else None)
                if isinstance(payload, dict):
                    _datadog_headers = {}
                    Propagator.inject(ctx.span.context, _datadog_headers)
                    payload = dict(payload)  # shallow copy to avoid mutating caller's dict
                    payload["_datadog"] = _datadog_headers
                    if "payload" in kwargs:
                        kwargs = dict(kwargs)
                        kwargs["payload"] = payload
                    elif len(args) > 1:
                        args = (args[0], payload) + args[2:]
            except Exception:
                pass

        try:
            result = func(*args, **kwargs)
        except SuspendExecution:
            ctx.dispatch_ended_event()
            raise
        except BaseException:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        ctx.dispatch_ended_event()
        return result


# ---------------------------------------------------------------------------
# Factory for simple internal operations (wait, callback, map, etc.)
# ---------------------------------------------------------------------------

def _make_traced_internal(span_name, default_resource):
    """Create a wrapper for an internal durable operation.

    All internal operations follow the same tracing pattern: extract an
    optional ``name`` kwarg for the resource, create an internal span,
    handle SuspendExecution as non-error control flow, and propagate
    real exceptions with error tags.
    """
    def _traced(func, instance, args, kwargs):
        from aws_durable_execution_sdk_python.exceptions import SuspendExecution

        name = kwargs.get("name")

        event = TracingEvent.create(
            component=config.aws_durable_execution_sdk_python.integration_name,
            integration_config=config.aws_durable_execution_sdk_python,
            operation_name=span_name,
            span_type="worker",
            span_kind="internal",
            resource=name or default_resource,
            service=_get_service(),
        )

        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            try:
                result = func(*args, **kwargs)
            except SuspendExecution:
                ctx.dispatch_ended_event()
                raise
            except BaseException:
                ctx.dispatch_ended_event(*sys.exc_info())
                raise
            ctx.dispatch_ended_event()
            return result

    return _traced


_traced_wait = _make_traced_internal("aws.durable_execution.wait", "wait")
_traced_wait_for_condition = _make_traced_internal("aws.durable_execution.wait_for_condition", "wait_for_condition")
_traced_wait_for_callback = _make_traced_internal("aws.durable_execution.wait_for_callback", "wait_for_callback")
_traced_create_callback = _make_traced_internal("aws.durable_execution.create_callback", "create_callback")
_traced_map = _make_traced_internal("aws.durable_execution.map", "map")
_traced_parallel = _make_traced_internal("aws.durable_execution.parallel", "parallel")
_traced_run_in_child_context = _make_traced_internal("aws.durable_execution.child_context", "child_context")


# ---------------------------------------------------------------------------
# patch / unpatch
# ---------------------------------------------------------------------------

def _try_wrap(module_path, target, wrapper, label):
    """Wrap *target* in *module_path*, logging gracefully on failure."""
    try:
        wrap(module_path, target, wrapper)
    except (ImportError, AttributeError):
        log.debug("Could not wrap %s (may not exist in this SDK version)", label)


def patch():
    """Instrument the aws-durable-execution-sdk-python library."""
    if getattr(aws_durable_execution_sdk_python, "_datadog_patch", False):
        return

    aws_durable_execution_sdk_python._datadog_patch = True

    # --- durable_execution decorator ---
    # AIDEV-NOTE: durable_execution is re-exported from the top-level __init__
    # via ``from .execution import durable_execution``. That creates a separate
    # name binding, so we must wrap in BOTH modules.
    wrap("aws_durable_execution_sdk_python.execution", "durable_execution", _traced_durable_execution)
    wrap("aws_durable_execution_sdk_python", "durable_execution", _traced_durable_execution)

    # --- DurableContext methods (always present) ---
    wrap("aws_durable_execution_sdk_python.context", "DurableContext.step", _traced_step)
    wrap("aws_durable_execution_sdk_python.context", "DurableContext.invoke", _traced_invoke)

    # --- DurableContext methods (may not exist in older SDK versions) ---
    _try_wrap("aws_durable_execution_sdk_python.context", "DurableContext.wait", _traced_wait, "DurableContext.wait")
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.wait_for_condition",
        _traced_wait_for_condition,
        "DurableContext.wait_for_condition",
    )
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.wait_for_callback",
        _traced_wait_for_callback,
        "DurableContext.wait_for_callback",
    )
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.create_callback",
        _traced_create_callback,
        "DurableContext.create_callback",
    )
    _try_wrap("aws_durable_execution_sdk_python.context", "DurableContext.map", _traced_map, "DurableContext.map")
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.parallel",
        _traced_parallel,
        "DurableContext.parallel",
    )
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.run_in_child_context",
        _traced_run_in_child_context,
        "DurableContext.run_in_child_context",
    )

    # --- ThreadPoolExecutor patching for map/parallel trace propagation ---
    try:
        import aws_durable_execution_sdk_python.concurrency.executor as executor_module
        executor_module.ThreadPoolExecutor = TracedThreadPoolExecutor
    except (ImportError, AttributeError):
        log.debug("Could not patch ThreadPoolExecutor (concurrency module may not exist)")


def unpatch():
    """Remove instrumentation from the aws-durable-execution-sdk-python library."""
    if not getattr(aws_durable_execution_sdk_python, "_datadog_patch", False):
        return

    aws_durable_execution_sdk_python._datadog_patch = False

    from aws_durable_execution_sdk_python import execution
    from aws_durable_execution_sdk_python.context import DurableContext

    unwrap(execution, "durable_execution")
    unwrap(aws_durable_execution_sdk_python, "durable_execution")
    unwrap(DurableContext, "step")
    unwrap(DurableContext, "invoke")

    # Unwrap optional methods — only if they were wrapped (i.e. have __wrapped__)
    for method_name in (
        "wait",
        "wait_for_condition",
        "wait_for_callback",
        "create_callback",
        "map",
        "parallel",
        "run_in_child_context",
    ):
        method = getattr(DurableContext, method_name, None)
        if method is not None and hasattr(method, "__wrapped__"):
            unwrap(DurableContext, method_name)

    # Restore original ThreadPoolExecutor
    try:
        import aws_durable_execution_sdk_python.concurrency.executor as executor_module
        executor_module.ThreadPoolExecutor = _OriginalThreadPoolExecutor
    except (ImportError, AttributeError):
        pass
