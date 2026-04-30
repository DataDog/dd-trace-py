"""Unit tests for the trace-context checkpoint writer.

These exercise the diff/encode/checkpoint-id logic without bringing up a real
DurableContext or hitting the SDK's checkpoint queue.
"""

from types import SimpleNamespace
from unittest import mock

import pytest
import ujson

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


def test_stable_headers_strips_volatile():
    raw = {
        "x-datadog-trace-id": "1",
        "x-datadog-parent-id": "999",
        "x-datadog-tags": "_dd.p.tid=abc",
        # tracestate carries:
        #   - dd= vendor section with sampling, decision-maker, parent id, etc.
        #   - a non-dd vendor entry that should pass through untouched
        "tracestate": "dd=s:1;t.dm:-0;p:badf00d;o:rum,othervendor=keep",
    }
    stable = trace_checkpoint._stable_headers(raw)
    assert "x-datadog-parent-id" not in stable
    assert stable["x-datadog-trace-id"] == "1"
    assert stable["x-datadog-tags"] == "_dd.p.tid=abc"
    # Only the `p:` entry inside dd= is dropped; everything else stays.
    assert stable["tracestate"] == "dd=s:1;t.dm:-0;o:rum,othervendor=keep"


def test_strip_dd_parent_from_tracestate_handles_edge_cases():
    f = trace_checkpoint._strip_dd_parent_from_tracestate
    # Only `p:` in the dd vendor → segment dropped entirely
    assert f("dd=p:abc") == ""
    # Multiple non-dd vendors are preserved verbatim
    assert f("vendor1=a,vendor2=b") == "vendor1=a,vendor2=b"
    # Whitespace around segments and kv pairs is normalized
    assert f("dd= s:1 ; p:abc , v=x") == "dd=s:1,v=x"
    # `p:` only stripped at the start of a kv entry — `t.parent` is preserved
    assert f("dd=t.parent:keep;p:drop") == "dd=t.parent:keep"
    # Empty string passes through
    assert f("") == ""


def test_diff_ignores_only_dd_parent_change():
    """Two saves where only ``dd=p:`` differs must compare equal so the second
    one is suppressed. Changing other dd vendor entries (sampling priority,
    origin) should NOT be suppressed.
    """
    base = {
        "x-datadog-trace-id": "1",
        "tracestate": "dd=s:1;t.dm:-0;p:aaaa,vendor=keep",
    }
    only_p_changed = {
        "x-datadog-trace-id": "1",
        "tracestate": "dd=s:1;t.dm:-0;p:bbbb,vendor=keep",
    }
    sampling_changed = {
        "x-datadog-trace-id": "1",
        "tracestate": "dd=s:2;t.dm:-0;p:aaaa,vendor=keep",
    }
    assert trace_checkpoint._stable_headers(base) == trace_checkpoint._stable_headers(only_p_changed)
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


def test_save_uses_grandparent_span_id_on_first_invocation():
    state = _make_state()
    durable = SimpleNamespace(state=state, _parent_id=None)
    # span tree: root(0xR) → aws.lambda(0xL) → aws.durable.execute(0xX)
    span = _span_chain(0x4242_4242_4242_4242, 0x9999_9999_9999_9999, 0x1234_1234_1234_1234)

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "222"  # current span's id, will be overridden

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    update, is_sync = state._captured[0]
    assert is_sync is False
    assert update.name == "_datadog_0"
    assert update.parent_id is None  # AWS-side parent stays None at top level
    payload = ujson.loads(update.payload)
    # Grandparent id (the durable-execution root) is stamped into the Datadog header.
    assert payload["x-datadog-parent-id"] == str(0x4242_4242_4242_4242)


def test_save_reuses_prior_checkpoint_parent_id_on_replay():
    """On replay, ``state.operations`` has a ``_datadog_*`` carrying
    the parent id from a prior invocation. Reuse it verbatim instead of
    walking the (now incomplete) span tree.
    """
    prior_payload = ujson.dumps({"x-datadog-trace-id": "111", "x-datadog-parent-id": "5555555555555555"})
    state = _make_state(
        operations={
            "id-0": SimpleNamespace(
                name="_datadog_0",
                step_details=SimpleNamespace(result=prior_payload),
            ),
        }
    )
    durable = SimpleNamespace(state=state, _parent_id=None)
    # On replay datadog-lambda doesn't emit aws.durable-execution; the local
    # span tree only has aws.lambda → aws.durable.execute. Grandparent walk
    # would yield aws.lambda's id, which is wrong — we should reuse the prior
    # checkpoint instead.
    span = _span_chain(0xDEAD_BEEF_DEAD_BEEF, 0x1234_1234_1234_1234)

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "current-span"

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    payload = ujson.loads(state._captured[0][0].payload)
    assert payload["x-datadog-parent-id"] == "5555555555555555"


def test_save_rewrites_traceparent_parent_segment():
    state = _make_state()
    durable = SimpleNamespace(state=state, _parent_id=None)
    span = _span_chain(0x4242_4242_4242_4242, 0x9999_9999_9999_9999, 0x1234_1234_1234_1234)

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "deadbeef"
        headers["traceparent"] = "00-0000000000000000000000000000007b-feedfacecafebabe-01"

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    payload = ujson.loads(state._captured[0][0].payload)
    parts = payload["traceparent"].split("-")
    assert parts[0] == "00"
    assert parts[1] == "0000000000000000000000000000007b"
    assert parts[2] == format(0x4242_4242_4242_4242, "016x")
    assert parts[3] == "01"


def test_save_first_invocation_then_replay_share_parent_id():
    """End-to-end: invocation 1 stamps grandparent id; invocation 2 (replay)
    reads the saved checkpoint and reuses the same parent id. Both saved
    payloads must carry the identical ``x-datadog-parent-id``.
    """
    arn = "arn:aws:lambda:us-east-2:1:function:f:1/durable-execution/wf/abc-123"
    root_id = 0xCAFE_BABE_CAFE_BABE

    # Invocation 1: root span exists in the process, no prior checkpoint.
    state1 = _make_state()
    state1.durable_execution_arn = arn
    durable1 = SimpleNamespace(state=state1, _parent_id=None)
    span1 = _span_chain(root_id, 0xAAAA, 0xBBBB)

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "current-span"

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable1, span1)

    pid_1 = ujson.loads(state1._captured[0][0].payload)["x-datadog-parent-id"]
    assert pid_1 == str(root_id)

    # Invocation 2 (replay): root span no longer in this process, but the
    # prior checkpoint is in state.operations. The save reuses pid_1.
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

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable2, span2)

    pid_2 = ujson.loads(state2._captured[0][0].payload)["x-datadog-parent-id"]
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

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert state._captured[0][0].parent_id == "ctx-op-7"


def test_save_skips_when_headers_unchanged():
    state = _make_state()
    durable = SimpleNamespace(state=state, _parent_id=None)
    span = _fake_span()

    def _inject(_span, headers):
        headers["x-datadog-trace-id"] = "111"

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert len(state._captured) == 1


def test_save_skips_when_matches_prior_checkpoint_in_state_operations():
    """A new invocation reads the latest existing ``_datadog_*`` from
    ``state.operations``. If the new (override-applied, stable) headers match
    that prior checkpoint's stable headers, no new checkpoint is written —
    even though the in-memory ``_dd_last_propagation_headers`` stash is empty
    at the start of the invocation. This is the regression test for
    accumulated identical checkpoints across invocations.
    """
    # The prior checkpoint includes a different `dd=p:` (per-span volatile)
    # value than what the new save will produce — the diff must ignore it
    # and treat the headers as equivalent.
    prior_payload = ujson.dumps(
        {
            "x-datadog-trace-id": "111",
            "x-datadog-parent-id": "ROOT",
            "x-datadog-sampling-priority": "1",
            "tracestate": "dd=s:1;t.dm:-0;p:OLDPARENT,vendor=keep",
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
        # parent id and dd=p: rotated to a new value.
        headers["x-datadog-trace-id"] = "111"
        headers["x-datadog-parent-id"] = "current-span"
        headers["x-datadog-sampling-priority"] = "1"
        headers["tracestate"] = "dd=s:1;t.dm:-0;p:NEWPARENT,vendor=keep"

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert state._captured == []  # no new checkpoint enqueued


def test_save_writes_when_real_context_differs_from_prior():
    """If something stable changed (e.g. sampling priority), a new checkpoint
    is written even though parent-id and dd=p have rotated.
    """
    prior_payload = ujson.dumps(
        {
            "x-datadog-trace-id": "111",
            "x-datadog-parent-id": "ROOT",
            "x-datadog-sampling-priority": "1",
            "tracestate": "dd=s:1;t.dm:-0;p:OLDPARENT",
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
        headers["tracestate"] = "dd=s:2;t.dm:-0;p:NEWPARENT"

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
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

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
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

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=_inject):
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


def test_save_no_op_when_state_missing():
    durable = SimpleNamespace(state=None, _parent_id=None)
    trace_checkpoint.maybe_save_trace_context_checkpoint(durable, _fake_span())
    # No exception, no calls — nothing to assert beyond not crashing.


def test_save_no_op_when_inject_raises():
    state = _make_state()
    durable = SimpleNamespace(state=state, _parent_id=None)
    span = _fake_span()

    with mock.patch.object(trace_checkpoint.HTTPPropagator, "inject", side_effect=RuntimeError("boom")):
        trace_checkpoint.maybe_save_trace_context_checkpoint(durable, span)

    assert state._captured == []
