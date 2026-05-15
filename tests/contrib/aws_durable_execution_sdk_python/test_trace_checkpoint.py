"""Unit tests for the trace-context checkpoint writer.

These exercise the diff/encode/checkpoint-id logic without bringing up a real
DurableContext or hitting the SDK's checkpoint queue.
"""

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from ddtrace.contrib.internal.aws_durable_execution_sdk_python import trace_checkpoint
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import patch
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import unpatch


@pytest.fixture(autouse=True)
def patched():
    patch()
    yield
    unpatch()


def _make_state(operations=None):
    """Build a duck-typed state. Captures every create_checkpoint call."""
    captured = []

    def create_checkpoint(update, is_sync=True):
        captured.append((update, is_sync))

    state = SimpleNamespace(
        durable_execution_arn="arn:aws:lambda:us-east-2:1:function:f:1/durable-execution/wf/abc-123",
        operations=operations or {},
        create_checkpoint=create_checkpoint,
    )
    state._captured = captured
    return state


def _fake_span(trace_id=0xAAAABBBB, span_id=0xCCCCDDDD):
    span = mock.MagicMock()
    span.trace_id = trace_id
    span.span_id = span_id
    span.context = mock.MagicMock(trace_id=trace_id, span_id=span_id)
    return span


def test_step_id_is_deterministic():
    a = trace_checkpoint._step_id("_datadog_0", "arn:1")
    b = trace_checkpoint._step_id("_datadog_0", "arn:1")
    c = trace_checkpoint._step_id("_datadog_0", "arn:2")
    assert a == b
    assert a != c
    # blake2b w/ digest_size=16 → 32 hex chars
    assert len(a) == 32


def test_stable_headers_strips_x_datadog_parent_id():
    """``x-datadog-parent-id`` rotates per span and must be normalized away;
    everything else in the Datadog header set is stable across replays.
    """
    raw = {
        "x-datadog-trace-id": "1",
        "x-datadog-parent-id": "999",
        "x-datadog-sampling-priority": "1",
        "x-datadog-tags": "_dd.p.tid=abc",
    }
    stable = trace_checkpoint._stable_headers(raw)
    assert "x-datadog-parent-id" not in stable
    assert stable == {
        "x-datadog-trace-id": "1",
        "x-datadog-sampling-priority": "1",
        "x-datadog-tags": "_dd.p.tid=abc",
    }


def test_diff_ignores_only_parent_id_change():
    """Two saves where only ``x-datadog-parent-id`` differs must compare equal
    so the second is suppressed.  Sampling-priority changes must NOT be
    suppressed — they're a real context change.
    """
    base = {"x-datadog-trace-id": "1", "x-datadog-parent-id": "AAAA", "x-datadog-sampling-priority": "1"}
    only_pid_changed = {"x-datadog-trace-id": "1", "x-datadog-parent-id": "BBBB", "x-datadog-sampling-priority": "1"}
    sampling_changed = {"x-datadog-trace-id": "1", "x-datadog-parent-id": "AAAA", "x-datadog-sampling-priority": "2"}
    assert trace_checkpoint._stable_headers(base) == trace_checkpoint._stable_headers(only_pid_changed)
    assert trace_checkpoint._stable_headers(base) != trace_checkpoint._stable_headers(sampling_changed)


def _span_chain(*span_ids):
    """Build a doubly-linked span chain mimicking ``aws.durable-execution`` →
    ``aws.lambda`` → ``aws.durable.execute``.

    Returns the leaf (innermost) span. Each entry in ``span_ids`` is the
    span_id for that level, *outermost first*.
    """
    cur = None
    for sid in span_ids:
        s = mock.MagicMock()
        s.span_id = sid
        s._parent = cur
        cur = s
    return cur


def test_save_uses_execute_span_id_on_first_invocation():
    state = _make_state()
    durable = SimpleNamespace(state=state, _parent_id=None)
    # span tree: root(0xR) → aws.lambda(0xL) → aws.durable.execute(0xX)
    span = _span_chain(0x4242_4242_4242_4242, 0x9999_9999_9999_9999, 0x1234_1234_1234_1234)

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "222"  # current span's id, will be overridden

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    update, is_sync = state._captured[0]
    assert is_sync is True
    assert update.name == "_datadog_0"
    assert update.parent_id is None  # AWS-side parent stays None at top level
    payload = json.loads(update.payload)
    # First invocation anchors to aws.durable.execute span id.
    assert payload["x-datadog-parent-id"] == str(0x1234_1234_1234_1234)


def test_save_reuses_prior_checkpoint_parent_id_on_replay():
    """On replay, ``state.operations`` has a ``_datadog_*`` carrying
    the parent id from a prior invocation. Reuse it verbatim instead of
    walking the (now incomplete) span tree.

    Use a different sampling priority between prior and current so the
    diff-suppression layer doesn't short-circuit the save — we need an
    actual write to observe which parent id was stamped.
    """
    prior_payload = json.dumps(
        {
            "x-datadog-trace-id": "111",
            "x-datadog-parent-id": "5555555555555555",
            "x-datadog-sampling-priority": "1",
        }
    )
    state = _make_state(
        operations={
            "id-0": SimpleNamespace(
                name="_datadog_0",
                step_details=SimpleNamespace(result=prior_payload),
            ),
        }
    )
    durable = SimpleNamespace(state=state, _parent_id=None)
    # On replay we should always reuse the parent id already persisted in the
    # prior checkpoint.
    span = _span_chain(0xDEAD_BEEF_DEAD_BEEF, 0x1234_1234_1234_1234)

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "current-span"
        headers["x-datadog-sampling-priority"] = "2"

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    payload = json.loads(state._captured[0][0].payload)
    assert payload["x-datadog-parent-id"] == "5555555555555555"


def test_save_first_invocation_then_replay_share_parent_id():
    """End-to-end: invocation 1 stamps execute span id; invocation 2 (replay)
    reads the saved checkpoint and reuses the same parent id. Both saved
    payloads must carry the identical ``x-datadog-parent-id``.

    Replay bumps the sampling priority so the diff-suppression layer
    doesn't short-circuit invocation 2's save.
    """
    arn = "arn:aws:lambda:us-east-2:1:function:f:1/durable-execution/wf/abc-123"
    anchor_id = 0xBBBB

    # Invocation 1: no prior checkpoint, anchor to execute span id.
    state1 = _make_state()
    state1.durable_execution_arn = arn
    durable1 = SimpleNamespace(state=state1, _parent_id=None)
    span1 = _span_chain(0xCAFE_BABE_CAFE_BABE, 0xAAAA, anchor_id)

    def _inject_invocation_1(_span, headers):
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "current-span"
        headers["x-datadog-sampling-priority"] = "1"

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=_inject_invocation_1):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable1, span1)

    pid_1 = json.loads(state1._captured[0][0].payload)["x-datadog-parent-id"]
    assert pid_1 == str(anchor_id)

    # Invocation 2 (replay): prior checkpoint is in state.operations.
    # The save reuses pid_1.  Different sampling priority ensures a real save.
    persisted = state1._captured[0][0]
    state2 = _make_state(
        operations={
            persisted.operation_id: SimpleNamespace(
                name=persisted.name,
                step_details=SimpleNamespace(result=persisted.payload),
            ),
        }
    )
    state2.durable_execution_arn = arn
    durable2 = SimpleNamespace(state=state2, _parent_id=None)
    # Different span tree on replay (no durable-execution root span exists).
    span2 = _span_chain(0xDEAD, 0xBEEF)

    def _inject_invocation_2(_span, headers):
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "current-span"
        headers["x-datadog-sampling-priority"] = "2"

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=_inject_invocation_2):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable2, span2)

    pid_2 = json.loads(state2._captured[0][0].payload)["x-datadog-parent-id"]
    assert pid_2 == pid_1


def test_save_inherits_parent_id_from_child_context():
    state = _make_state()
    # Inside a run_in_child_context, durable_context._parent_id is the
    # parent CONTEXT operation id — this is what AWS expects to see on
    # any STEP we append inside that child.
    durable = SimpleNamespace(state=state, _parent_id="ctx-op-7")
    span = _fake_span()

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "1"

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert state._captured[0][0].parent_id == "ctx-op-7"


def test_save_skips_when_matches_prior_checkpoint_in_state_operations():
    """A new invocation reads the latest existing ``_datadog_*`` from
    ``state.operations``. If the new stable headers match that prior
    checkpoint's stable headers, no new checkpoint is written. This is the
    regression test for accumulated identical checkpoints across invocations.

    Only ``x-datadog-parent-id`` rotates per span — the diff layer must
    ignore it and treat headers with the same other fields as equivalent.
    """
    prior_payload = json.dumps(
        {
            "x-datadog-trace-id": "111",
            "x-datadog-parent-id": "ROOT",
            "x-datadog-sampling-priority": "1",
        }
    )
    state = _make_state(
        operations={
            "id-0": SimpleNamespace(
                name="_datadog_0",
                step_details=SimpleNamespace(result=prior_payload),
            ),
        }
    )
    durable = SimpleNamespace(state=state, _parent_id=None)
    # Span tree mimics a replay: aws.lambda → aws.durable.execute (no root
    # in this process). Override resolution will pull "ROOT" from the prior.
    span = _span_chain(0xAAAA, 0xBBBB)

    def _inject(_span, headers):
        # Same logical context as the prior checkpoint, but with the per-span
        # parent id rotated.
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "current-span"
        headers["x-datadog-sampling-priority"] = "1"

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert state._captured == []  # no new checkpoint enqueued


def test_save_writes_when_real_context_differs_from_prior():
    """If something stable changed (e.g. sampling priority), a new checkpoint
    is written even though the per-span parent id has rotated.
    """
    prior_payload = json.dumps(
        {
            "x-datadog-trace-id": "111",
            "x-datadog-parent-id": "ROOT",
            "x-datadog-sampling-priority": "1",
        }
    )
    state = _make_state(
        operations={
            "id-0": SimpleNamespace(
                name="_datadog_0",
                step_details=SimpleNamespace(result=prior_payload),
            ),
        }
    )
    durable = SimpleNamespace(state=state, _parent_id=None)
    span = _span_chain(0xAAAA, 0xBBBB)

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "x"
        headers["x-datadog-sampling-priority"] = "2"  # ← changed

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert len(state._captured) == 1
    update = state._captured[0][0]
    assert update.name == "_datadog_1"  # next number past the prior


def test_save_increments_n_when_headers_change():
    state = _make_state()
    durable = SimpleNamespace(state=state, _parent_id=None)
    span = _fake_span()

    counter = {"n": 0}

    def _inject(_span, headers):
        counter["n"] += 1
        headers["x-datadog-trace-id"] = str(counter["n"])

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=_inject):
        # Two writes in quick succession — neither has been persisted to
        # state.operations yet (still in the SDK's batch queue).  The counter
        # must still produce distinct N values.
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert [u.name for u, _ in state._captured] == [
        "_datadog_0",
        "_datadog_1",
    ]
    # All checkpoints in the same context share the same parent_id (top-level → None).
    assert state._captured[0][0].parent_id is None
    assert state._captured[1][0].parent_id is None
    # Operation ids must be distinct so AWS doesn't reject the batch.
    assert state._captured[0][0].operation_id != state._captured[1][0].operation_id


def test_counter_resumes_past_existing_replay_checkpoints():
    """On a replay, state.operations already contains _datadog_0..K.
    The next allocated N must be K+1 (not 0), to avoid colliding with the
    deterministic blake2b id of an already-persisted checkpoint.
    """
    state = _make_state(
        operations={
            "id-3": SimpleNamespace(name="_datadog_3"),
            "id-1": SimpleNamespace(name="_datadog_1"),
            # User step in between — must not affect numbering.
            "user-step-x": SimpleNamespace(name="my_step"),
        }
    )
    durable = SimpleNamespace(state=state, _parent_id=None)
    span = _fake_span()

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "abc"

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert state._captured[0][0].name == "_datadog_4"


def test_concurrent_allocate_yields_distinct_ns():
    """Threaded callers must get distinct N values — this is what prevents
    'Cannot update the same operation twice in a single request' when the
    SDK batches concurrent step finishes.
    """
    import threading as _threading

    state = _make_state()
    n_threads = 25
    iterations = 4
    seen = []
    seen_lock = _threading.Lock()

    def _worker():
        for _ in range(iterations):
            n = trace_checkpoint._allocate_checkpoint_n(state)
            with seen_lock:
                seen.append(n)

    threads = [_threading.Thread(target=_worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == n_threads * iterations
    assert len(set(seen)) == len(seen)  # no duplicates
    assert sorted(seen) == list(range(len(seen)))  # contiguous from 0


def test_mark_visited_calls_track_replay_only_for_datadog_ops():
    """The marker delegates to ``state.track_replay`` for every ``_datadog_*``
    op and ignores everything else.  This is what the SDK does from inside
    ``DurableContext.*`` for the customer's own steps.
    """
    calls = []
    state = SimpleNamespace(
        operations={
            "u-1": SimpleNamespace(name="user_step"),
            "d-0": SimpleNamespace(name="_datadog_0"),
            "d-1": SimpleNamespace(name="_datadog_1"),
            "u-2": SimpleNamespace(name="another_step"),
        },
        track_replay=calls.append,
    )

    trace_checkpoint.mark_trace_context_checkpoints_visited(state)

    assert sorted(calls) == ["d-0", "d-1"]


def test_mark_visited_no_op_when_no_datadog_ops():
    """No ``_datadog_*`` ops → no track_replay calls (zero overhead path)."""
    calls = []
    state = SimpleNamespace(
        operations={"u-1": SimpleNamespace(name="user_step")},
        track_replay=calls.append,
    )

    trace_checkpoint.mark_trace_context_checkpoints_visited(state)

    assert calls == []


def test_mark_visited_swallows_track_replay_errors():
    """A misbehaving SDK or replaced ``track_replay`` must not break the
    user's workflow — checkpoint visiting is observability, not correctness.
    """

    def _boom(_op_id):
        raise RuntimeError("nope")

    state = SimpleNamespace(
        operations={"d-0": SimpleNamespace(name="_datadog_0")},
        track_replay=_boom,
    )

    trace_checkpoint.mark_trace_context_checkpoints_visited(state)  # does not raise


def test_mark_visited_unblocks_sdk_replay_transition():
    """End-to-end with a real ``ExecutionState``: after marking, the SDK
    transitions REPLAY → NEW as soon as user code visits its own op.

    Without ``mark_trace_context_checkpoints_visited`` the subset check
    ``completed_ops.issubset(_visited_operations)`` would stay false forever
    because the customer's code never calls track_replay for our ops.
    """
    from aws_durable_execution_sdk_python.lambda_service import Operation
    from aws_durable_execution_sdk_python.lambda_service import OperationStatus
    from aws_durable_execution_sdk_python.lambda_service import OperationType
    from aws_durable_execution_sdk_python.state import ExecutionState
    from aws_durable_execution_sdk_python.state import ReplayStatus

    def _op(op_id, name):
        return Operation(
            operation_id=op_id,
            operation_type=OperationType.STEP,
            status=OperationStatus.SUCCEEDED,
            name=name,
        )

    state = ExecutionState(
        durable_execution_arn="arn:aws:lambda:us-east-2:1:function:f:1/durable-execution/wf/abc-123",
        initial_checkpoint_token="token",
        operations={op.operation_id: op for op in [_op("u-1", "user_step"), _op("d-0", "_datadog_0")]},
        service_client=mock.Mock(),
        replay_status=ReplayStatus.REPLAY,
    )

    trace_checkpoint.mark_trace_context_checkpoints_visited(state)

    # Our checkpoint is visited; user op still pending → REPLAY holds.
    assert state.is_replaying()

    # User code reaches its step → SDK now sees all completed ops visited.
    state.track_replay("u-1")
    assert not state.is_replaying()


def test_save_no_op_when_state_missing():
    durable = SimpleNamespace(state=None, _parent_id=None)
    trace_checkpoint.maybe_save_trace_context_checkpoint(durable, _fake_span())
    # No exception, no calls — nothing to assert beyond not crashing.


def test_save_no_op_when_inject_raises():
    state = _make_state()
    durable = SimpleNamespace(state=state, _parent_id=None)
    span = _fake_span()

    with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=RuntimeError("boom")):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert state._captured == []


def test_record_integration_error_emits_telemetry_count_metric():
    """``_record_integration_error`` emits an ``integration_errors`` count
    metric tagged with the integration name, the exception class name, and
    the call-site ``location``.  The ``location`` tag is what lets us tell
    which code path failed without parsing logs.
    """
    with mock.patch.object(trace_checkpoint.telemetry_writer, "add_count_metric") as mock_add:
        trace_checkpoint._record_integration_error(RuntimeError("boom"), "inject")

    mock_add.assert_called_once_with(
        trace_checkpoint.TELEMETRY_NAMESPACE.TRACERS,
        "integration_errors",
        1,
        (
            ("integration_name", "aws_durable_execution_sdk_python"),
            ("error_type", "RuntimeError"),
            ("location", "inject"),
        ),
    )


def test_record_integration_error_swallows_telemetry_failures():
    """Telemetry is best-effort — a writer failure must not propagate."""
    with mock.patch.object(
        trace_checkpoint.telemetry_writer, "add_count_metric", side_effect=RuntimeError("telemetry down")
    ):
        trace_checkpoint._record_integration_error(ValueError("inner"), "inject")  # does not raise


def test_swallowed_paths_record_integration_error():
    """All ``except Exception`` paths in trace_checkpoint route through the
    telemetry helper with a distinct ``location`` tag.  Covers: mark_visited
    and header inject.  Each location must appear so we can disambiguate
    failures by code path from telemetry alone.
    """
    state = _make_state()
    durable = SimpleNamespace(state=state, _parent_id=None)

    with mock.patch.object(trace_checkpoint, "_record_integration_error") as mock_rec:
        # 1. mark_visited: track_replay raises → location="mark_visited"
        bad_state = SimpleNamespace(
            operations={"d-0": SimpleNamespace(name="_datadog_0")},
            track_replay=mock.Mock(side_effect=RuntimeError("mark")),
        )
        trace_checkpoint.mark_trace_context_checkpoints_visited(bad_state)

        # 2. maybe_save: inject raises → location="inject"
        with mock.patch.object(trace_checkpoint, "_inject_datadog_headers", side_effect=RuntimeError("inject")):
            trace_checkpoint.maybe_save_trace_context_checkpoint(durable, _fake_span())

    assert mock_rec.call_count >= 2
    error_types = [type(call.args[0]).__name__ for call in mock_rec.call_args_list]
    assert "RuntimeError" in error_types
    locations = [call.args[1] for call in mock_rec.call_args_list]
    assert "mark_visited" in locations
    assert "inject" in locations
