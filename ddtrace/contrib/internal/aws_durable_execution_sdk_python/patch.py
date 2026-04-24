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
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator as Propagator


log = get_logger(__name__)

_CHECKPOINT_NAME_PREFIX = "_dd_trace_context_"
_TERMINAL_STATUSES = ("SUCCEEDED", "FAILED")
_DURABLE_DEBUG_REV = "checkpoint-debug-v3-2026-04-24"


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


def _start_integration_span(operation_name, span_type, span_kind, resource, tags=None, parent_context=None):
    """Create and activate a span without relying on span.lifecycle handlers.

    AIDEV-NOTE: We intentionally avoid ``core.context_with_event(TracingEvent)``
    here to maintain compatibility with runtimes where span.lifecycle handlers
    are unavailable/misaligned, which otherwise causes fallback ``default`` spans.
    """
    from ddtrace import tracer

    integration_name = getattr(
        config.aws_durable_execution_sdk_python, "integration_name", "aws_durable_execution_sdk_python"
    )
    child_of = parent_context if parent_context is not None else tracer.context_provider.active()
    span = tracer.start_span(
        operation_name,
        child_of=child_of,
        service=_get_service(),
        resource=resource,
        span_type=span_type,
        activate=True,
    )
    span.set_tag("component", integration_name)
    span.set_tag("span.kind", span_kind)
    for k, v in (tags or {}).items():
        if v is not None:
            span.set_tag(k, v)
    return span


def _checkpoint_debug(message, **fields):
    """Emit debug prints for checkpoint decision flow."""
    try:
        if fields:
            print("[DD-DURABLE][checkpoint] {} {}".format(message, fields))
        else:
            print("[DD-DURABLE][checkpoint] {}".format(message))
    except Exception:
        # Debug logging must never impact application flow.
        pass


def _checkpoint_header_summary(headers):
    if not isinstance(headers, dict):
        return {"valid": False, "type": type(headers).__name__}
    return {
        "trace_id": headers.get("x-datadog-trace-id"),
        "parent_id": headers.get("x-datadog-parent-id"),
        "sampling_priority": headers.get("x-datadog-sampling-priority"),
        "traceparent": headers.get("traceparent"),
        "tracestate": headers.get("tracestate"),
        "keys": sorted(headers.keys()),
    }


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


def _normalize_tracestate_ignoring_dd_parent(tracestate):
    """Normalize ``tracestate`` by removing Datadog's ``p:`` member.

    ``p:`` encodes the current parent span id and is expected to differ across
    invocations. For checkpoint dedupe we treat those differences as equivalent.
    """
    if not isinstance(tracestate, str):
        return tracestate

    members = [part.strip() for part in tracestate.split(",") if part.strip()]
    normalized_members = []

    for member in members:
        if not member.startswith("dd="):
            normalized_members.append(member)
            continue

        _, _, value = member.partition("=")
        if not value:
            continue

        dd_parts = []
        for dd_part in value.split(";"):
            dd_part = dd_part.strip()
            if not dd_part:
                continue
            if dd_part.startswith("p:"):
                continue
            dd_parts.append(dd_part)

        if dd_parts:
            normalized_members.append("dd={}".format(";".join(dd_parts)))

    return ",".join(normalized_members)


def _normalize_headers_for_checkpoint_comparison(headers):
    """Return normalized headers for checkpoint dedupe comparisons.

    Differences in parent-id fields and W3C tracestate ``dd.p`` are ignored.
    """
    result = dict(headers)
    result.pop("x-datadog-parent-id", None)
    tp = result.get("traceparent")
    if isinstance(tp, str):
        parts = tp.split("-")
        if len(parts) == 4:
            parts[2] = "0" * 16
            result["traceparent"] = "-".join(parts)
    ts = result.get("tracestate")
    if isinstance(ts, str):
        result["tracestate"] = _normalize_tracestate_ignoring_dd_parent(ts)
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


def _current_trace_root_span_id():
    """Best-effort lookup of the current trace root span id.

    This is used by the suspend fallback path when the decorator-level
    ``aws.durable_execution.execute`` span is unavailable and we still need
    to persist a checkpoint parent.
    """
    try:
        from ddtrace import tracer

        root = tracer.current_root_span()
        if root is not None:
            return root.span_id
    except Exception:
        pass
    return None


def _save_trace_context_checkpoint(state, number, headers):
    """Create a ``_dd_trace_context_{number}`` STEP operation with the payload."""
    from aws_durable_execution_sdk_python.identifier import OperationIdentifier
    from aws_durable_execution_sdk_python.lambda_service import OperationUpdate

    name = "{}{}".format(_CHECKPOINT_NAME_PREFIX, number)
    arn = getattr(state, "durable_execution_arn", "") or ""
    op_id = hashlib.blake2b("{}:{}".format(name, arn).encode()).hexdigest()[:64]
    payload = json.dumps(headers)

    identifier = OperationIdentifier(operation_id=op_id, name=name)

    _checkpoint_debug(
        "Saving checkpoint operation",
        checkpoint_name=name,
        checkpoint_number=number,
        operation_id=op_id,
        arn=arn,
        header_summary=_checkpoint_header_summary(headers),
    )
    state.create_checkpoint(
        operation_update=OperationUpdate.create_step_start(identifier),
        is_sync=True,
    )
    _checkpoint_debug(
        "Checkpoint START persisted",
        checkpoint_name=name,
        operation_id=op_id,
    )
    state.create_checkpoint(
        operation_update=OperationUpdate.create_step_succeed(identifier, payload),
        is_sync=True,
    )
    _checkpoint_debug(
        "Checkpoint SUCCEED persisted",
        checkpoint_name=name,
        operation_id=op_id,
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
    _checkpoint_debug(
        "Entering checkpoint save decision",
        has_span=span is not None,
        has_durable_context=durable_context is not None,
        grandparent_span_id=grandparent_span_id,
        result_status=result.get("Status") if isinstance(result, dict) else None,
    )
    if span is None or durable_context is None:
        _checkpoint_debug(
            "Skipping checkpoint save: missing span or durable context",
            has_span=span is not None,
            has_durable_context=durable_context is not None,
        )
        return
    state = getattr(durable_context, "state", None)
    if state is None:
        _checkpoint_debug("Skipping checkpoint save: durable context has no state")
        return

    # Skip for terminal statuses — no next invocation
    if isinstance(result, dict):
        status = result.get("Status")
        if status in _TERMINAL_STATUSES:
            _checkpoint_debug(
                "Skipping checkpoint save: terminal status",
                status=status,
            )
            return

    # Inject current trace context into headers
    try:
        current_headers = {}
        Propagator.inject(span.context, current_headers)
        _checkpoint_debug(
            "Injected current span context into headers",
            current_header_summary=_checkpoint_header_summary(current_headers),
        )
    except Exception as e:
        _checkpoint_debug(
            "Skipping checkpoint save: failed to inject current context",
            error=str(e),
        )
        return
    if not current_headers:
        _checkpoint_debug("Skipping checkpoint save: injected headers empty")
        return

    existing = _find_trace_context_checkpoints(state)
    _checkpoint_debug(
        "Discovered existing checkpoint operations",
        existing_count=len(existing),
        checkpoints=[
            {"number": number, "name": getattr(op, "name", None), "id": getattr(op, "operation_id", None)}
            for (number, op) in existing
        ],
    )

    if not existing:
        # First checkpoint: _dd_trace_context_0, parent_id = grandparent (root)
        new_number = 0
        _checkpoint_debug(
            "No existing checkpoints found; preparing first checkpoint",
            new_checkpoint_number=new_number,
            grandparent_span_id=grandparent_span_id,
        )
        if grandparent_span_id is not None:
            _override_parent_id(current_headers, grandparent_span_id)
            _checkpoint_debug(
                "Applied grandparent as checkpoint parent",
                updated_header_summary=_checkpoint_header_summary(current_headers),
            )
    else:
        latest_number, latest_op = existing[-1]
        latest_headers = _parse_checkpoint_payload(latest_op)
        if latest_headers is None:
            _checkpoint_debug(
                "Skipping checkpoint save: latest checkpoint payload is not parseable",
                latest_checkpoint_number=latest_number,
                latest_checkpoint_name=getattr(latest_op, "name", None),
                latest_checkpoint_id=getattr(latest_op, "operation_id", None),
            )
            return
        _checkpoint_debug(
            "Loaded latest checkpoint payload",
            latest_checkpoint_number=latest_number,
            latest_checkpoint_name=getattr(latest_op, "name", None),
            latest_header_summary=_checkpoint_header_summary(latest_headers),
        )

        # Warn if trace_id differs — should never happen in a single execution
        cur_tid = _headers_trace_id(current_headers)
        prev_tid = _headers_trace_id(latest_headers)
        if cur_tid and prev_tid and cur_tid != prev_tid:
            log.warning(
                "Trace ID mismatch between checkpoints: current=%s previous=%s",
                cur_tid,
                prev_tid,
            )
            _checkpoint_debug(
                "Trace ID mismatch detected",
                current_trace_id=cur_tid,
                previous_trace_id=prev_tid,
            )

        # Skip save if trace context is unchanged for checkpoint semantics.
        normalized_current = _normalize_headers_for_checkpoint_comparison(current_headers)
        normalized_latest = _normalize_headers_for_checkpoint_comparison(latest_headers)
        if normalized_current == normalized_latest:
            _checkpoint_debug(
                "Skipping checkpoint save: normalized context unchanged",
                normalized_current=_checkpoint_header_summary(normalized_current),
                normalized_latest=_checkpoint_header_summary(normalized_latest),
            )
            return

        new_number = latest_number + 1
        # Reuse previous checkpoint's parent_id (same grandparent)
        _override_parent_id(current_headers, _headers_parent_id(latest_headers))
        _checkpoint_debug(
            "Context changed; preparing next checkpoint",
            new_checkpoint_number=new_number,
            updated_header_summary=_checkpoint_header_summary(current_headers),
            previous_parent_id=_headers_parent_id(latest_headers),
        )

    try:
        _save_trace_context_checkpoint(state, new_number, current_headers)
        _checkpoint_debug(
            "Checkpoint save complete",
            checkpoint_number=new_number,
            saved_header_summary=_checkpoint_header_summary(current_headers),
        )
    except Exception as e:
        log.debug("Failed to save trace context checkpoint: %s", e)
        _checkpoint_debug(
            "Checkpoint save failed with exception",
            checkpoint_number=new_number,
            error=str(e),
        )


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
        _checkpoint_debug(
            "Entered traced durable_execution user function",
            debug_rev=_DURABLE_DEBUG_REV,
            input_event_type=type(input_event).__name__,
            has_durable_context=durable_context is not None,
            durable_context_type=type(durable_context).__name__ if durable_context is not None else None,
        )

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

        execution_span = _start_integration_span(
            "aws.durable_execution.execute",
            span_type="serverless",
            span_kind="server",
            resource="aws.durable_execution.execute",
            tags=tags,
            parent_context=distributed_context,
        )
        try:
            result = user_func(*inner_args, **inner_kwargs)
        except SuspendExecution:
            # AIDEV-NOTE: SuspendExecution is control flow, not an error.
            # Save checkpoint before closing the span so the span is still
            # active (propagator.inject reads from span.context).
            _maybe_save_trace_context_checkpoint(
                execution_span, durable_context, grandparent_span_id, None
            )
            execution_span.finish()
            raise
        except BaseException:
            _maybe_save_trace_context_checkpoint(
                execution_span, durable_context, grandparent_span_id, None
            )
            execution_span.set_exc_info(*sys.exc_info())
            execution_span.finish()
            raise
        _maybe_save_trace_context_checkpoint(
            execution_span, durable_context, grandparent_span_id, result
        )
        execution_span.finish()
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

    step_span = _start_integration_span(
        "aws.durable_execution.step",
        span_type="worker",
        span_kind="internal",
        resource=step_name or "step",
        tags=tags,
    )
    try:
        result = func(*args, **kwargs)
    except SuspendExecution:
        _try_tag_replayed(step_span, step_func, _step_executed)
        step_span.finish()
        raise
    except BaseException:
        _try_tag_replayed(step_span, step_func, _step_executed)
        step_span.set_exc_info(*sys.exc_info())
        step_span.finish()
        raise
    _try_tag_replayed(step_span, step_func, _step_executed)
    step_span.finish()
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

    invoke_span = _start_integration_span(
        "aws.durable_execution.invoke",
        span_type="serverless",
        span_kind="client",
        resource=invoke_name or function_name or "invoke",
        tags=tags,
    )
    try:
        # --- Inject distributed tracing context into payload ---
        # AIDEV-NOTE: When the payload is a dict, we inject trace context under a
        # "_datadog" key so the downstream @durable_execution handler can extract it
        # and link its execution span to this invoke span's trace.
        if config.aws_durable_execution_sdk_python.distributed_tracing_enabled:
            try:
                if "payload" in kwargs:
                    payload = kwargs.get("payload")
                else:
                    payload = args[1] if len(args) > 1 else None
                if isinstance(payload, dict):
                    _datadog_headers = {}
                    Propagator.inject(invoke_span.context, _datadog_headers)
                    payload = dict(payload)  # shallow copy to avoid mutating caller's dict
                    existing_datadog_headers = payload.get("_datadog")
                    if isinstance(existing_datadog_headers, dict):
                        merged_datadog_headers = dict(existing_datadog_headers)
                        merged_datadog_headers.update(_datadog_headers)
                        payload["_datadog"] = merged_datadog_headers
                    else:
                        payload["_datadog"] = _datadog_headers
                    if "payload" in kwargs:
                        kwargs = dict(kwargs)
                        kwargs["payload"] = payload
                    elif len(args) > 1:
                        args = (args[0], payload) + args[2:]
            except Exception:
                pass

        result = func(*args, **kwargs)
    except SuspendExecution:
        invoke_span.finish()
        raise
    except BaseException:
        invoke_span.set_exc_info(*sys.exc_info())
        invoke_span.finish()
        raise
    invoke_span.finish()
    return result


# ---------------------------------------------------------------------------
# Factory for simple internal operations (wait, callback, map, etc.)
# ---------------------------------------------------------------------------

def _make_traced_internal(span_name, default_resource, checkpoint_on_suspend=False):
    """Create a wrapper for an internal durable operation.

    All internal operations follow the same tracing pattern: extract an
    optional ``name`` kwarg for the resource, create an internal span,
    handle SuspendExecution as non-error control flow, and propagate
    real exceptions with error tags.
    """
    def _traced(func, instance, args, kwargs):
        from aws_durable_execution_sdk_python.exceptions import SuspendExecution

        name = kwargs.get("name")

        span = _start_integration_span(
            span_name,
            span_type="worker",
            span_kind="internal",
            resource=name or default_resource,
        )
        try:
            result = func(*args, **kwargs)
        except SuspendExecution:
            if checkpoint_on_suspend:
                root_span_id = _current_trace_root_span_id()
                _checkpoint_debug(
                    "SuspendExecution caught in internal span; running checkpoint fallback",
                    operation=span_name,
                    resource=name or default_resource,
                    root_span_id=root_span_id,
                    has_durable_context=instance is not None and hasattr(instance, "state"),
                )
                try:
                    _maybe_save_trace_context_checkpoint(
                        span=span,
                        durable_context=instance,
                        grandparent_span_id=root_span_id,
                        result=None,
                    )
                except Exception as e:
                    _checkpoint_debug(
                        "Checkpoint fallback on suspend raised exception",
                        operation=span_name,
                        error=str(e),
                    )
            span.finish()
            raise
        except BaseException:
            span.set_exc_info(*sys.exc_info())
            span.finish()
            raise
        span.finish()
        return result

    return _traced


_traced_wait = _make_traced_internal(
    "aws.durable_execution.wait",
    "wait",
    checkpoint_on_suspend=True,
)
_traced_wait_for_condition = _make_traced_internal(
    "aws.durable_execution.wait_for_condition",
    "wait_for_condition",
    checkpoint_on_suspend=True,
)
_traced_wait_for_callback = _make_traced_internal(
    "aws.durable_execution.wait_for_callback",
    "wait_for_callback",
    checkpoint_on_suspend=True,
)
_traced_create_callback = _make_traced_internal("aws.durable_execution.create_callback", "create_callback")
_traced_map = _make_traced_internal("aws.durable_execution.map", "map")
_traced_parallel = _make_traced_internal("aws.durable_execution.parallel", "parallel")
_traced_run_in_child_context = _make_traced_internal(
    "aws.durable_execution.child_context",
    "child_context",
    checkpoint_on_suspend=True,
)


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

    print(
        "[DD-DURABLE][integration] patch() loaded "
        "rev={} file={}".format(_DURABLE_DEBUG_REV, __file__)
    )
    aws_durable_execution_sdk_python._datadog_patch = True

    # --- durable_execution decorator ---
    # AIDEV-NOTE: durable_execution is re-exported from the top-level __init__
    # via ``from .execution import durable_execution``. That creates a separate
    # name binding, so we must wrap in BOTH modules.
    wrap("aws_durable_execution_sdk_python.execution", "durable_execution", _traced_durable_execution)
    wrap("aws_durable_execution_sdk_python", "durable_execution", _traced_durable_execution)
    try:
        import aws_durable_execution_sdk_python.execution as execution_module

        print(
            "[DD-DURABLE][integration] durable_execution wrap status "
            "rev={} top_level_wrapped={} execution_wrapped={}".format(
                _DURABLE_DEBUG_REV,
                hasattr(aws_durable_execution_sdk_python.durable_execution, "__wrapped__"),
                hasattr(execution_module.durable_execution, "__wrapped__"),
            )
        )
    except Exception as e:
        print(
            "[DD-DURABLE][integration] durable_execution wrap status unavailable "
            "rev={} error={}".format(_DURABLE_DEBUG_REV, e)
        )

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
