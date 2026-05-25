"""Persist Datadog trace context as a STEP operation in the durable execution log.

On ``SuspendExecution``, appends a synthetic STEP ``_datadog_{N}`` whose payload
is the Datadog propagation headers for the current trace.  The next invocation
reads the highest N from ``InitialExecutionState.Operations`` to re-activate the trace.

Only runs on the suspend path; no-op when stable headers (``x-datadog-parent-id``
excluded) match the most recent prior checkpoint.

AIDEV-NOTE: only Datadog-style headers are written — both writer and reader are
Datadog code (this integration and ``datadog-lambda-python``), so W3C/B3 headers
would just bloat the payload.

AIDEV-NOTE: ``_datadog_*`` is a reserved step name; the SDK does not enforce this.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from aws_durable_execution_sdk_python.context import DurableContext
from aws_durable_execution_sdk_python.identifier import OperationIdentifier
from aws_durable_execution_sdk_python.lambda_service import OperationUpdate
from aws_durable_execution_sdk_python.state import ExecutionState

from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.threads import Lock
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.propagation.http import _DatadogMultiHeader


log = get_logger(__name__)


_INTEGRATION_NAME = "aws_durable_execution_sdk_python"


def _record_integration_error(exc: BaseException, location: str) -> None:
    """Emit an ``integration_errors`` count metric to Datadog internal telemetry.

    Paired with ``log.debug`` at each catch-and-continue site.  Must never raise.
    ``location`` is a low-cardinality call-site tag — keep the value set bounded.
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
# Module-global lock serializing N allocation. ExecutionState is shared across
# worker threads (parallel/map); the critical section is microseconds, so a
# single lock is simpler than per-state locks and not a measurable bottleneck.
_COUNTER_LOCK = Lock()


def _inject_datadog_headers(span: Span, headers: dict) -> None:
    """Inject only Datadog headers (ignores ``DD_TRACE_PROPAGATION_STYLE_INJECT``).

    Routes through ``_get_sampled_injection_context`` to force a sampling
    decision before trace_id / sampling_priority are read.
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

    ``track_replay`` only transitions REPLAY → NEW when every completed op in
    ``state.operations`` is in ``_visited_operations``. Our checkpoints are
    completed STEP ops that user code never visits, so without this they pin
    the SDK in REPLAY for the rest of the invocation — silently suppressing
    ``context.logger`` and anything else gated on ``is_replaying()``.
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

    Process-local counter on the state — ``state.operations`` only updates
    after the SDK's ~1s checkpoint batch lands, so counting from it would let
    racing threads pick the same ``N`` and produce colliding blake2b op_ids
    (AWS then rejects: "Cannot update the same operation twice in a single
    request").
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

    On replay invocations the highest-N checkpoint serves two purposes: its
    ``x-datadog-parent-id`` becomes the stamped anchor parent for the new save
    (see ``_resolve_override_parent_id``), and its stable headers are diffed
    against ours to suppress redundant writes.
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

    Reuse the prior checkpoint's saved parent id verbatim (stable across all
    invocations of the same durable execution), or anchor to the current
    ``aws.durable.execute`` span id on the very first save.
    """
    if prior_payload is not None:
        pid = prior_payload.get("x-datadog-parent-id")
        if pid is not None:
            return str(pid)
    anchor_span_id = getattr(span, "span_id", None)
    return str(anchor_span_id) if anchor_span_id is not None else None


def _override_parent_id(headers: dict, parent_id: str) -> None:
    """Replace the injector's current span id with the resolved anchor id."""
    if "x-datadog-parent-id" in headers:
        headers["x-datadog-parent-id"] = parent_id


def maybe_save_trace_context_checkpoint(durable_context: DurableContext, span: Span) -> None:
    """Append a ``_datadog_{N}`` STEP if propagation headers changed.

    Called once per invocation on the ``SuspendExecution`` path. No-op when the
    stable headers match the most recent existing ``_datadog_*`` checkpoint.
    All errors are swallowed — failure here must never break the workflow.
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

        # Diff on stable headers (parent-id stripped); skip writing if unchanged.
        # The parent-id override is applied later, only when we actually save.
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

        # AWS validates parent_id against real operations — use the durable
        # context's parent (None at top level = "child of the EXECUTION"); a
        # Datadog span id here would raise InvalidParameterValueException.
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
