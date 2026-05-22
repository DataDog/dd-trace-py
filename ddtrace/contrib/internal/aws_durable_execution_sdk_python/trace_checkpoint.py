"""Persist Datadog trace context as a STEP operation in the durable execution log.

When a durable handler suspends (``SuspendExecution``) the integration appends
a synthetic STEP operation named ``_datadog_{N}`` whose payload is a JSON dict
of the propagation headers for the current trace context.  On the next
invocation, ``datadog-lambda-python`` reads the highest ``N`` out of
``InitialExecutionState.Operations`` and re-activates the trace.

The save runs only on the suspend path: a workflow that returns or fails for
good has no further invocations to read the checkpoint, so writing one would
be wasted work and would be rejected by the SDK's terminating checkpointer.
The save is also a no-op when the new headers match the most recent prior
checkpoint (after stripping the per-span ``x-datadog-parent-id``) — every
replay would otherwise rewrite identical context and pile up redundant
operations.

AIDEV-NOTE: only Datadog-style headers are written.  Both ends of this
checkpoint are Datadog code (this integration writes, ``datadog-lambda-python``
reads), so we ignore ``DD_TRACE_PROPAGATION_STYLE_INJECT`` entirely.  A
customer running ``b3`` or ``tracecontext`` upstream still gets a Datadog
checkpoint here — they don't need W3C/B3 inside the durable log.

AIDEV-NOTE: ``_datadog_*`` is a reserved step name. Users must not create
steps with this prefix; the SDK does not enforce this — we rely on it being
unusual enough not to collide.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Optional

from aws_durable_execution_sdk_python.context import DurableContext
from aws_durable_execution_sdk_python.identifier import OperationIdentifier
from aws_durable_execution_sdk_python.lambda_service import OperationUpdate
from aws_durable_execution_sdk_python.state import ExecutionState

from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.propagation.http import _DatadogMultiHeader


log = get_logger(__name__)


_INTEGRATION_NAME = "aws_durable_execution_sdk_python"


def _record_integration_error(exc: BaseException, location: str) -> None:
    """Emit an ``integration_errors`` count metric for a swallowed exception.

    Goes to Datadog internal telemetry (visible to us), not customer logs.
    Paired with ``log.debug`` everywhere we catch-and-continue so the failure
    is observable to Datadog without exposing internal exceptions to the
    customer's log pipeline.  Must never raise — telemetry is best-effort.

    ``location`` is a short, low-cardinality identifier for the call site
    (e.g. ``"inject"``, ``"create_checkpoint"``) so the metric tells us
    *which* code path failed without us having to fish through customer
    logs.  Keep the set of values bounded — it becomes a metric tag.
    """
    try:
        telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.TRACERS,
            "integration_errors",
            1,
            (
                ("integration_name", _INTEGRATION_NAME),
                ("error_type", type(exc).__name__),
                ("location", location),
            ),
        )
    except Exception:  # nosec B110
        pass


_CHECKPOINT_NAME_PREFIX = "_datadog_"
_STATE_NEXT_N_ATTR = "_dd_next_checkpoint_n"
# Module-global lock serializing checkpoint-N allocation across all states.
# ExecutionState is shared across worker threads (parallel/map), so two threads
# can race on the counter.  The critical section is microseconds and the SDK
# already serializes per-state work in practice, so a single lock is simpler
# than per-state locks and not a measurable bottleneck.
_COUNTER_LOCK = threading.Lock()


def _inject_datadog_headers(span: Span, headers: dict) -> None:
    """Inject only Datadog-style propagation headers, ignoring the customer's
    ``DD_TRACE_PROPAGATION_STYLE_INJECT`` setting.

    Both ends of this checkpoint are Datadog code (this integration writes,
    ``datadog-lambda-python`` reads), so emitting W3C/B3 alongside would just
    bloat the payload and complicate the diff layer.  We still route through
    ``_get_sampled_injection_context`` so a sampling decision is triggered
    before the trace id / sampling priority are read out.
    """
    span_context = HTTPPropagator._get_sampled_injection_context(span, None)
    _DatadogMultiHeader._inject(span_context, headers)


def _stable_headers(headers: dict) -> dict:
    """Drop the per-span ``x-datadog-parent-id`` so the diff is meaningful.

    That is the only field that rotates per span across replays; everything
    else in the Datadog header set is stable for a given trace context.
    """
    return {k: v for k, v in headers.items() if k.lower() != "x-datadog-parent-id"}


def _max_existing_checkpoint_n(state) -> int:
    """The highest ``N`` already present in ``state.operations``, or -1 if none."""
    operations = getattr(state, "operations", None) or {}
    highest = -1
    for op in operations.values():
        name = getattr(op, "name", None)
        if not name or not name.startswith(_CHECKPOINT_NAME_PREFIX):
            continue
        suffix = name[len(_CHECKPOINT_NAME_PREFIX) :]
        try:
            n = int(suffix)
        except ValueError:
            continue
        if n > highest:
            highest = n
    return highest


def mark_trace_context_checkpoints_visited(state: ExecutionState) -> None:
    """Tell the SDK our ``_datadog_*`` ops are already visited.

    The SDK's ``track_replay`` transitions REPLAY → NEW only when every
    completed op in ``state.operations`` is in ``_visited_operations``.
    Our checkpoints are completed STEP ops that user code never visits,
    so without this they would keep the SDK in REPLAY for the rest of the
    invocation — which silently suppresses ``context.logger`` output and
    anything else gated on ``is_replaying()``.

    Mirrors what would happen if the customer had written these steps:
    ``DurableContext.*`` methods all call ``state.track_replay(op_id)``
    before doing anything else; we do the same for our ops at invocation
    start.  ``track_replay`` itself acquires ``_replay_status_lock`` and
    runs the subset check, so we don't need to touch SDK internals.
    """
    op_id = None
    try:
        for op_id, op in list(state.operations.items()):
            if op.name and op.name.startswith(_CHECKPOINT_NAME_PREFIX):
                state.track_replay(op_id)
    except Exception as e:
        log.debug(
            "mark_trace_context_checkpoints_visited failed at op_id=%s",
            op_id,
            exc_info=True,
        )
        _record_integration_error(e, "mark_visited")


def _allocate_checkpoint_n(state) -> int:
    """Atomically reserve the next ``N`` for a checkpoint write.

    The SDK batches checkpoints for up to ~1s before sending; if two threads
    both reuse the same ``N`` the resulting blake2b operation_ids collide and
    AWS rejects the batch with "Cannot update the same operation twice in a
    single request".  Counting only ``state.operations`` would also collide,
    because AWS doesn't update that until the batch lands.  So we keep a
    process-local counter on the state itself.
    """
    with _COUNTER_LOCK:
        n = getattr(state, _STATE_NEXT_N_ATTR, None)
        if n is None:
            n = _max_existing_checkpoint_n(state) + 1
        try:
            setattr(state, _STATE_NEXT_N_ATTR, n + 1)
        except Exception as e:
            log.debug(
                "Could not advance checkpoint counter to N=%d on %s",
                n + 1,
                type(state).__name__,
                exc_info=True,
            )
            _record_integration_error(e, "allocate_n")
            return -1
        return n


def _step_id(name: str, execution_arn: str) -> str:
    """Deterministic blake2b-based step id so re-runs don't duplicate."""
    digest = hashlib.blake2b(f"{name}:{execution_arn}".encode("utf-8"), digest_size=16).hexdigest()
    return digest


def _read_prior_checkpoint_payload(state) -> Optional[dict]:
    """Parsed headers dict from the highest-N ``_datadog_*`` operation, or ``None``.

    On replay invocations, ``state.operations`` already contains the
    checkpoints written by previous invocations. We use the highest-numbered
    one for two purposes:

    - **Parent id reuse** — its ``x-datadog-parent-id`` is the value to stamp
      into the new save (so all checkpoints across all invocations parent off
      the same execute-span anchor; see ``_resolve_override_parent_id``).
    - **Diff suppression** — comparing its stable headers to ours tells us
      whether the trace context actually changed since the last save.
    """
    operations = getattr(state, "operations", None) or {}
    best_op = None
    best_n = -1
    for op in operations.values():
        name = getattr(op, "name", None)
        if not name or not name.startswith(_CHECKPOINT_NAME_PREFIX):
            continue
        suffix = name[len(_CHECKPOINT_NAME_PREFIX) :]
        try:
            n = int(suffix)
        except ValueError:
            continue
        if n > best_n:
            best_n = n
            best_op = op
    if best_op is None:
        return None
    step_details = getattr(best_op, "step_details", None)
    payload_str = getattr(step_details, "result", None) if step_details is not None else None
    if not payload_str:
        return None
    try:
        payload = json.loads(payload_str)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _resolve_override_parent_id(span, prior_payload: Optional[dict]) -> Optional[str]:
    """Resolve the parent id to stamp into the saved checkpoint.

    1. **Prior checkpoint** — if any ``_datadog_*`` already exists on the
       state (i.e. this is a replay invocation), reuse its saved parent id
       verbatim. Across all invocations of the same durable execution this
       stays stable.
    2. **Current execute span** — on the very first save, anchor to the
       current ``aws.durable.execute`` span id.
    """
    if prior_payload is not None:
        pid = prior_payload.get("x-datadog-parent-id")
        if pid is not None:
            return str(pid)
    anchor_span_id = getattr(span, "span_id", None)
    return str(anchor_span_id) if anchor_span_id is not None else None


def _override_parent_id(headers: dict, parent_id: str) -> None:
    """Stamp ``parent_id`` into ``x-datadog-parent-id``.

    The injector writes the *current* span id; we replace it with the resolved
    anchor id so every checkpoint across replays parents off the same span.
    """
    if "x-datadog-parent-id" in headers:
        headers["x-datadog-parent-id"] = parent_id


def maybe_save_trace_context_checkpoint(durable_context: DurableContext, span: Span) -> None:
    """Append a ``_datadog_{N}`` STEP if propagation headers changed.

    Called once per invocation, on the ``SuspendExecution`` path of the
    durable-execution wrapper.  No-op when the propagation headers match the
    most recent existing ``_datadog_*`` operation in ``state.operations``
    (after stripping per-span volatile fields), so identical context across
    replays does not pile up redundant checkpoints.

    Failure here must never break the workflow; all errors are swallowed.
    """
    try:
        state = getattr(durable_context, "state", None)
        if state is None:
            return
        execution_arn = getattr(state, "durable_execution_arn", None)
        if not execution_arn:
            return

        headers: dict = {}
        try:
            _inject_datadog_headers(span, headers)
        except Exception as e:
            log.debug(
                "Datadog header injection failed for span_id=%s",
                getattr(span, "span_id", None),
                exc_info=True,
            )
            _record_integration_error(e, "inject")
            return
        if not headers:
            return

        prior_payload = _read_prior_checkpoint_payload(state)

        # ``_stable_headers`` strips ``x-datadog-parent-id`` and the
        # ``dd=p:`` segment, so the override does not affect the diff —
        # defer it until we know we're actually saving.  Suppress when prior
        # matches: trace context hasn't changed.
        stable = _stable_headers(headers)
        if prior_payload is not None and _stable_headers(prior_payload) == stable:
            return

        # Stamp the anchor parent id so every checkpoint across replays
        # references the same execute span.  Without this, each invocation
        # would inject its own current span id and fragment the trace tree.
        override_pid = _resolve_override_parent_id(span, prior_payload)
        if override_pid is not None:
            _override_parent_id(headers, override_pid)

        # Allocate ``N`` only after the diff so no-op calls don't burn numbers.
        n = _allocate_checkpoint_n(state)
        if n < 0:
            return
        name = f"{_CHECKPOINT_NAME_PREFIX}{n}"
        operation_id = _step_id(name, execution_arn)

        # AWS validates parent_id against real operations in the execution,
        # so we mirror what the SDK does for the user's own steps: parent off
        # the current durable context's parent (None at the top level, which
        # AWS accepts as "child of the EXECUTION").  Using a Datadog span id
        # here would fail with InvalidParameterValueException.
        parent_id: Optional[str] = getattr(durable_context, "_parent_id", None)

        identifier = OperationIdentifier(operation_id=operation_id, parent_id=parent_id, name=name)
        payload = json.dumps(headers, separators=(",", ":"))
        update = OperationUpdate.create_step_succeed(identifier, payload)

        # Use Sync to avoid being abandoned: the SDK drops unflushed async
        # checkpoints on suspend.  Cost is negligible — it's suspending anyway.
        try:
            state.create_checkpoint(update, is_sync=True)
        except Exception as e:
            log.debug(
                "Failed to write trace-context checkpoint name=%s op_id=%s",
                name,
                operation_id,
                exc_info=True,
            )
            _record_integration_error(e, "create_checkpoint")
    except Exception as e:
        log.debug(
            "maybe_save_trace_context_checkpoint failed for span_id=%s",
            getattr(span, "span_id", None),
            exc_info=True,
        )
        # Outer catch-all: anything we didn't anticipate inside the writer.
        _record_integration_error(e, "save_unknown")
