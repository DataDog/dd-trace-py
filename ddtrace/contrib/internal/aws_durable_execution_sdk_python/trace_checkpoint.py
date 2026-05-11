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
checkpoint (after stripping the per-span ``x-datadog-parent-id`` and the
``dd=p:`` entry of ``tracestate``) — every replay would otherwise rewrite
identical context and pile up redundant operations.

AIDEV-NOTE: ``_datadog_*`` is a reserved step name. Users must not create
steps with this prefix; the SDK does not enforce this — we rely on it being
unusual enough not to collide.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import TYPE_CHECKING
from typing import Optional

from aws_durable_execution_sdk_python.identifier import OperationIdentifier
from aws_durable_execution_sdk_python.lambda_service import OperationUpdate

from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator


if TYPE_CHECKING:
    from aws_durable_execution_sdk_python.context import DurableContext

    from ddtrace._trace.span import Span


log = get_logger(__name__)


_CHECKPOINT_NAME_PREFIX = "_datadog_"
_STATE_LAST_HEADERS_ATTR = "_dd_last_propagation_headers"
_STATE_NEXT_N_ATTR = "_dd_next_checkpoint_n"
# Module-global lock serializing checkpoint-N allocation across all states.
# ExecutionState is shared across worker threads (parallel/map), so two threads
# can race on the counter.  The critical section is microseconds and the SDK
# already serializes per-state work in practice, so a single lock is simpler
# than per-state locks and not a measurable bottleneck.
_COUNTER_LOCK = threading.Lock()


def _strip_dd_parent_from_tracestate(value: str) -> str:
    """Drop the ``p:`` entry from the ``dd=`` vendor section of ``tracestate``.

    The dd vendor's ``p:`` entry carries the current span's parent id and
    therefore changes on every span; everything else there (``s:``, ``t.dm:``,
    ``t.tid:``, ``o:``, …) is stable across the trace.  Stripping just ``p:``
    keeps the diff sensitive to *real* context changes — sampling priority,
    decision, origin, propagation tags — without false-positives from the
    parent id rotating each span.  Other vendors' segments are passed through
    untouched.
    """
    out_segments = []
    for seg in (s.strip() for s in value.split(",")):
        if not seg:
            continue
        if not seg.startswith("dd="):
            out_segments.append(seg)
            continue
        kvs = seg[len("dd=") :]
        kept = [kv for kv in (k.strip() for k in kvs.split(";")) if kv and not kv.startswith("p:")]
        if kept:
            out_segments.append("dd=" + ";".join(kept))
        # If only `p:` was present, drop the whole `dd=` segment.
    return ",".join(out_segments)


def _stable_headers(headers: dict) -> dict:
    """Strip per-span volatile fields so checkpoint diff is meaningful.

    Per RFC 7230 HTTP headers are case-insensitive, so we compare keys via
    ``.lower()``.  ``x-datadog-parent-id`` rotates per span; ``tracestate``'s
    ``dd=p:`` segment does too — both must be normalized away or the diff
    would think every span is a real context change.
    """
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl == "x-datadog-parent-id":
            continue
        if kl == "tracestate" and isinstance(v, str):
            normalized = _strip_dd_parent_from_tracestate(v)
            if normalized:
                out[k] = normalized
            continue
        out[k] = v
    return out


def _max_existing_checkpoint_n(state) -> int:
    """Highest ``N`` already present in ``state.operations``, or -1 if none.

    Scans for ``_datadog_{N}`` names and parses the suffix.  Used to
    seed the per-process counter on the very first checkpoint of an invocation
    so that replays continue numbering past whatever the previous invocation
    left behind, and so the (unlikely) case of a user step colliding on the
    name doesn't silently overwrite us.
    """
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
        except Exception:
            log.debug("Could not advance checkpoint counter", exc_info=True)
            return -1
        return n


def _step_id(name: str, execution_arn: str) -> str:
    """Deterministic blake2b-based step id so re-runs don't duplicate."""
    digest = hashlib.blake2b(f"{name}:{execution_arn}".encode("utf-8"), digest_size=16).hexdigest()
    return digest


# `traceparent` format: <version>-<trace_id>-<parent_id>-<flags>
_TRACEPARENT_RE = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


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
    """
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
    """Stamp ``parent_id`` into the Datadog and W3C parent-id fields.

    ``HTTPPropagator.inject`` writes the *current* span id; we replace it
    with the resolved anchor id so all replays parent off the same span.
    ``tracestate``'s ``dd=`` segment also carries a parent id, but rewriting
    it would mean re-encoding the vendor section — the Datadog extractor on
    the other end uses ``x-datadog-parent-id`` as the source of truth, so we
    leave ``tracestate`` alone (and the diff layer drops it anyway).
    """
    if "x-datadog-parent-id" in headers:
        headers["x-datadog-parent-id"] = parent_id
    tp = headers.get("traceparent")
    if isinstance(tp, str):
        m = _TRACEPARENT_RE.match(tp)
        if m is not None:
            try:
                new_span = format(int(parent_id), "016x")
            except ValueError:
                return
            headers["traceparent"] = f"{m.group(1)}-{m.group(2)}-{new_span}-{m.group(4)}"


def maybe_save_trace_context_checkpoint(durable_context: "DurableContext", span: "Span") -> None:
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
            HTTPPropagator.inject(span, headers)
        except Exception:
            log.debug("HTTPPropagator.inject failed", exc_info=True)
            return
        if not headers:
            return

        # One read of the prior checkpoint covers both uses below: the
        # diff suppression and the parent-id override.
        prior_payload = _read_prior_checkpoint_payload(state)

        # Diff first.  ``_stable_headers`` strips ``x-datadog-parent-id``
        # (it rotates per span) and the ``dd=p:`` segment of ``tracestate``,
        # so the override value does not participate in the comparison —
        # we can defer it until we know we're actually going to save.
        # Two suppression layers:
        #   1. Within this invocation: in-memory stash from the most recent
        #      successful save (defensive — current control flow saves at
        #      most once, but we don't want a future refactor to regress).
        #   2. Across invocations: parsed payload of the latest existing
        #      ``_datadog_*`` operation in ``state.operations``.
        # If either matches, nothing meaningful changed since the last write.
        stable = _stable_headers(headers)
        last_local = getattr(state, _STATE_LAST_HEADERS_ATTR, None)
        if last_local is not None and last_local == stable:
            return
        if prior_payload is not None and _stable_headers(prior_payload) == stable:
            return

        # Diff says we'll write — now resolve and stamp the anchor parent id
        # so every persisted checkpoint references the same execute anchor
        # across every replay.  The value is either (a) the current execute
        # span id (first-invocation save) or (b) whatever a prior checkpoint
        # already stored (replay save).  Without this, each invocation would
        # inject its own internal span id and fragment the trace tree.
        override_pid = _resolve_override_parent_id(span, prior_payload)
        if override_pid is not None:
            _override_parent_id(headers, override_pid)

        # Allocate ``N`` only after the diff says we'll actually write, so we
        # don't burn numbers on no-ops.  Counter is process-local: it starts at
        # ``max(N) + 1`` from state.operations on first use (handles replays
        # that already have prior checkpoints), then increments atomically.
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

        # Async checkpoint — observability only, must not block the workflow.
        try:
            state.create_checkpoint(update, is_sync=False)
        except Exception:
            log.debug("Failed to enqueue trace-context checkpoint", exc_info=True)
            return

        # Stash the headers we just persisted so the next call can diff and
        # avoid writing a no-op checkpoint.
        try:
            setattr(state, _STATE_LAST_HEADERS_ATTR, stable)
        except Exception:  # nosec B110
            # The checkpoint is already enqueued; this duplicate-write cache is best-effort.
            pass
    except Exception:
        log.debug("maybe_save_trace_context_checkpoint failed", exc_info=True)
