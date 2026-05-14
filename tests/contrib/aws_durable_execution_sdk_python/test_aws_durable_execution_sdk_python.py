import json

import aws_durable_execution_sdk_python as ades
from aws_durable_execution_sdk_python.config import Duration
from aws_durable_execution_sdk_python.config import StepConfig
from aws_durable_execution_sdk_python.retries import RetryStrategyConfig
from aws_durable_execution_sdk_python.retries import create_retry_strategy
from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner
import pytest

from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import patch
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import unpatch
from ddtrace.ext import aws_durable
from tests.utils import override_config


SNAPSHOT_IGNORES = [
    "meta.aws.durable.execution_arn",
    "meta.error.stack",
]


@pytest.fixture(autouse=True)
def patched():
    patch()
    yield
    unpatch()


def _fast_retry(max_attempts):
    return StepConfig(
        retry_strategy=create_retry_strategy(
            RetryStrategyConfig(max_attempts=max_attempts, initial_delay=Duration.from_seconds(1))
        )
    )


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_durable_execution(test_spans):
    """Happy-path workflow with no SDK operations: a single aws.durable.execute span."""

    @ades.durable_execution
    def workflow(event, context):
        return {"ok": True}

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run(input='{"hello": "world"}')

    assert test_spans.find_span(name="aws.durable.execute").get_tag(aws_durable.TAG_EXECUTION_ARN)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_step_with_retry():
    """Step that fails on attempt 1 and succeeds on attempt 2."""
    attempts = {"n": 0}

    def flaky(step_context):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient failure")
        return "ok"

    @ades.durable_execution
    def workflow(event, context):
        return context.step(flaky, name="flaky", config=_fast_retry(max_attempts=2))

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_replayed():
    """A wait suspends the workflow until its timer elapses. On the replay invocation
    the wait operation is read from its succeeded checkpoint, so its span carries
    aws.durable.replayed=true.
    """

    @ades.durable_execution
    def workflow(event, context):
        context.wait(Duration.from_seconds(1), name="wait-once")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_parallel_propagates_trace_context():
    """context.parallel uses TracedThreadPoolExecutor so child step spans inherit the
    trace_id and parent span_id from the parallel span across worker threads.
    """

    def work(step_context):
        return "done"

    def branch_a(child_ctx):
        return child_ctx.step(work, name="a")

    def branch_b(child_ctx):
        return child_ctx.step(work, name="b")

    @ades.durable_execution
    def workflow(event, context):
        return context.parallel([branch_a, branch_b], name="fan-out")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()


def test_replay_transitions_to_new_with_datadog_checkpoint():
    """Synthetic ``_datadog_*`` checkpoints must not pin the SDK into REPLAY.

    Regression for an integration bug: in invocation 1 the integration
    persists a ``_datadog_N`` STEP checkpoint via ``state.create_checkpoint``.
    On the resume invocation the SDK loads it as an initial operation and
    counts it toward ``completed_ops`` inside ``track_replay``.  But
    nothing in user code visits this op, so without our pre-marking the
    subset check ``completed_ops.issubset(_visited_operations)`` never
    holds and ``ExecutionState`` stays in REPLAY forever — which silently
    suppresses ``context.logger`` output on the resume invocation.

    This test drives a real ``DurableFunctionTestRunner`` end-to-end:
    a wait forces a suspend/resume, the integration writes a trace-context
    checkpoint in invocation 1, and on the resume invocation we record
    ``state.is_replaying()`` after user code catches up.  The SDK must
    have transitioned out of REPLAY.
    """
    snapshots: list[dict] = []
    invocation_index = {"n": 0}

    @ades.durable_execution
    def workflow(event, context):
        invocation_index["n"] += 1
        # context.wait suspends invocation 1; invocation 2 replays it.
        context.wait(Duration.from_seconds(1), name="pause")
        # A real step so the SDK's track_replay can drive REPLAY → NEW
        # once it sees user code has visited every completed op.
        context.step(lambda _ctx: "ok", name="finish")
        snapshots.append(
            {
                "invocation": invocation_index["n"],
                "is_replaying": context.state.is_replaying(),
                "op_names": sorted((getattr(op, "name", None) or "") for op in context.state.operations.values()),
            }
        )

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()

    # Invocation 1 raises SuspendExecution at context.wait, so it never
    # reaches the snapshot below — snapshots only contain successful runs.
    assert snapshots, "workflow never completed an invocation cleanly"

    final = snapshots[-1]
    # Guard: if no _datadog_* was ever written in the prior invocation, the
    # bug isn't being exercised at all and a green test would be vacuous.
    assert any(name.startswith("_datadog_") for name in final["op_names"]), (
        f"test setup error: no _datadog_* checkpoint present on resume, "
        f"so this test isn't exercising the bug. ops={final['op_names']!r}"
    )
    assert final["is_replaying"] is False, (
        f"After replay catches up, SDK must transition REPLAY → NEW even "
        f"with our _datadog_* in state.operations. snapshots={snapshots!r}"
    )


def test_persists_trace_context_checkpoint_across_suspend_resume():
    """A real suspend/resume must persist a well-formed ``_datadog_*``
    trace-context checkpoint that the resume invocation can read.

    Not a snapshot test on purpose:
      - The checkpoint payload lives in ``state.operations`` as a JSON
        string, not on a span, so it can't appear in span-snapshot JSON.
      - Cross-invocation trace continuity needs the extraction layer in
        datadog-lambda-python, which isn't loaded here — without it the
        two invocations emit two disconnected span trees, so a snapshot
        diff would have nothing meaningful to compare.
      - Header diff/anchor/counter logic is already covered by the focused
        unit tests in ``test_trace_checkpoint.py``. This test only proves
        that, inside a real SDK lifecycle, the integration writes a
        well-formed checkpoint that survives suspend/resume — i.e. the
        production plumbing is actually wired up end-to-end.
    """
    captured: list[dict] = []
    invocation_index = {"n": 0}

    @ades.durable_execution
    def workflow(event, context):
        invocation_index["n"] += 1
        # Forces invocation 1 to suspend; invocation 2 replays from the
        # persisted state — which is where the _datadog_* checkpoint
        # written in invocation 1 has to show up.
        context.wait(Duration.from_seconds(1), name="pause")
        # A real step keeps the SDK driving forward after replay so we
        # observe state.operations with all completed ops loaded.
        context.step(lambda _ctx: "ok", name="finish")
        datadog_ops = {
            (op.name or ""): getattr(getattr(op, "step_details", None), "result", None)
            for op in context.state.operations.values()
            if (getattr(op, "name", None) or "").startswith("_datadog_")
        }
        captured.append({"invocation": invocation_index["n"], "datadog_ops": datadog_ops})

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()

    # Invocation 1 raises SuspendExecution at context.wait and never reaches
    # the append below — captured only ever contains successful runs.
    assert captured, "workflow never completed an invocation cleanly"

    final = captured[-1]
    assert final["datadog_ops"], (
        f"resume invocation saw no _datadog_* checkpoint — integration did "
        f"not persist trace context across suspend/resume. captured={captured!r}"
    )

    # _datadog_0 is written in invocation 1 (before the suspend) and is the
    # one whose payload anchors to invocation 1's aws.durable.execute span.
    raw = final["datadog_ops"].get("_datadog_0")
    assert raw, f"_datadog_0 missing or has no persisted payload: {final['datadog_ops']!r}"
    headers = json.loads(raw)

    for key in ("x-datadog-trace-id", "x-datadog-parent-id", "traceparent"):
        assert headers.get(key), f"missing or empty header {key!r} in {headers!r}"

    # The integration rewrites traceparent's parent-id segment to match the
    # Datadog anchor, so the W3C view agrees with the Datadog view. If these
    # ever diverge a downstream extractor would build two different parents.
    tp_parent = headers["traceparent"].split("-")[2]
    expected = format(int(headers["x-datadog-parent-id"]), "016x")
    assert tp_parent == expected, (
        f"traceparent parent segment {tp_parent!r} != hex(x-datadog-parent-id) {expected!r} in {headers!r}"
    )


def test_cross_invocation_tracing_disabled_skips_checkpoint():
    """Opt-out path: with ``cross_invocation_tracing=False`` the integration
    must NOT persist a ``_datadog_*`` checkpoint on suspend.

    Mark-visited is intentionally left always-on (see patch.py), so this
    test only inspects the writer side.
    """
    captured: list[list[str]] = []

    @ades.durable_execution
    def workflow(event, context):
        context.wait(Duration.from_seconds(1), name="pause")
        context.step(lambda _ctx: "ok", name="finish")
        captured.append(
            [
                (getattr(op, "name", None) or "")
                for op in context.state.operations.values()
                if (getattr(op, "name", None) or "").startswith("_datadog_")
            ]
        )

    with override_config("aws_durable_execution_sdk_python", dict(cross_invocation_tracing=False)):
        with DurableFunctionTestRunner(workflow) as runner:
            runner.run()

    assert captured, "workflow never completed an invocation cleanly"
    assert captured[-1] == [], f"writer disabled but _datadog_* still persisted: {captured[-1]!r}"


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_workflow_failed_status():
    """Step that exhausts retries: terminal aws.durable.execute span has invocation_status=failed."""

    def always_fails(step_context):
        raise RuntimeError("permanent failure")

    @ades.durable_execution
    def workflow(event, context):
        return context.step(always_fails, name="always-fails", config=_fast_retry(max_attempts=1))

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()
