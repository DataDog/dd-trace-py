import json
import time
from unittest import mock

import aws_durable_execution_sdk_python as ades
from aws_durable_execution_sdk_python.config import Duration
from aws_durable_execution_sdk_python.config import StepConfig
from aws_durable_execution_sdk_python.retries import RetryStrategyConfig
from aws_durable_execution_sdk_python.retries import create_retry_strategy
from aws_durable_execution_sdk_python.waits import WaitForConditionConfig
from aws_durable_execution_sdk_python.waits import WaitForConditionDecision
from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner
import pytest

from ddtrace.contrib.internal.aws_durable_execution_sdk_python import patch as patch_module
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import _is_dynamic_parent
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import _traced_retry_handler
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import patch
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import unpatch
from ddtrace.ext import aws_durable
from ddtrace.internal import core
from ddtrace.internal.utils.version import parse_version
from tests.utils import override_config


SNAPSHOT_IGNORES = [
    "meta.aws.durable.execution_arn",
    "meta.error.stack",
]

SDK_VERSION = parse_version(ades.__version__)


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


def test_step_attempt_consistent_across_replay(test_spans):
    """A step that succeeds on its first attempt must report the same 0-indexed
    operation_attempt (0) whether the span is the fresh execution or a replay
    that reads the succeeded checkpoint.

    The succeeded checkpoint stores the 1-indexed number of the attempt that
    succeeded (1 for a first-try success), so without normalization the replay
    span would report 1 while the fresh execution reports 0.
    """

    def quick(step_context):
        return "ok"

    @ades.durable_execution
    def workflow(event, context):
        context.step(quick, name="quick", config=_fast_retry(max_attempts=1))
        # Suspends the workflow so it is re-invoked, replaying the already
        # succeeded step from its checkpoint.
        context.wait(Duration.from_seconds(1), name="pause")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()

    step_spans = list(test_spans.filter_spans(name=aws_durable.SPAN_STEP))
    assert len(step_spans) == 2, "expected a fresh-execution and a replay span for the step"
    for span in step_spans:
        assert span.get_metrics().get(aws_durable.TAG_OPERATION_ATTEMPT) == 0
    assert sorted(span.get_tag(aws_durable.TAG_REPLAYED) for span in step_spans) == ["false", "true"]


def test_failed_step_attempt_consistent_across_replay(test_spans):
    """A step that exhausts its retries and fails is, on replay, read from its FAILED
    checkpoint (the SDK re-raises the stored error without running the body). That replay
    span must be tagged replayed=true and report the same 0-indexed operation_attempt as
    the live final attempt.

    A FAILED checkpoint stores the 1-indexed attempt that failed (2 for a step whose 2nd
    attempt failed), so without normalization the replay span would report 2 while the live
    final attempt reports 1 -- the same off-by-one already handled for succeeded checkpoints.
    """

    def always_fails(step_context):
        raise RuntimeError("permanent failure")

    @ades.durable_execution
    def workflow(event, context):
        try:
            context.step(always_fails, name="doomed", config=_fast_retry(max_attempts=2))
        except Exception:
            # Swallow the permanent failure so the workflow continues to the wait below,
            # whose replay re-reads the now-FAILED step from its checkpoint.
            pass
        context.wait(Duration.from_seconds(1), name="pause")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()

    step_spans = list(test_spans.filter_spans(name=aws_durable.SPAN_STEP))
    replayed_spans = [s for s in step_spans if s.get_tag(aws_durable.TAG_REPLAYED) == "true"]
    assert replayed_spans, "expected a replay span for the failed step"
    for span in replayed_spans:
        assert span.get_metrics().get(aws_durable.TAG_OPERATION_ATTEMPT) == 1


def test_parallel_propagates_trace_context(test_spans):
    """context.parallel uses TracedThreadPoolExecutor so child step spans inherit the
    trace_id and parent span_id from the parallel span across worker threads.

    Branch executors are not traced, so they must not overwrite the parallel span's
    operation id with their own.
    """
    parallel_operation_ids = []

    def work(step_context):
        return "done"

    def branch_a(child_ctx):
        return child_ctx.step(work, name="a")

    def branch_b(child_ctx):
        return child_ctx.step(work, name="b")

    @ades.durable_execution
    def workflow(event, context):
        result = context.parallel([branch_a, branch_b], name="fan-out")
        parallel_operation_ids.extend(
            op.operation_id for op in context.state.operations.values() if op.name == "fan-out"
        )
        return result

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()

    parallel_span = test_spans.find_span(name=aws_durable.SPAN_PARALLEL)
    assert parallel_span.get_tag(aws_durable.TAG_ID) == parallel_operation_ids[0]
    step_spans = list(test_spans.filter_spans(name=aws_durable.SPAN_STEP))
    assert sorted(span.get_tag(aws_durable.TAG_NAME) for span in step_spans) == ["a", "b"]
    for step_span in step_spans:
        assert step_span.trace_id == parallel_span.trace_id
        assert step_span.parent_id == parallel_span.span_id


def _is_replaying(context):
    # SDK 3.x tracks replay status on the DurableContext; 1.x tracks it on the ExecutionState.
    if hasattr(context, "is_replaying"):
        return context.is_replaying()
    return context.state.is_replaying()


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
    the replay status after user code catches up.  The SDK must
    have transitioned out of REPLAY.  SDK 3.x tracks replay per context and
    has no ``track_replay``, so there it guards that our checkpoints still
    cannot pin REPLAY.
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
                "is_replaying": _is_replaying(context),
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

    for key in ("x-datadog-trace-id", "x-datadog-parent-id"):
        assert headers.get(key), f"missing or empty header {key!r} in {headers!r}"

    # Datadog-only style: no W3C/B3 keys should ever appear in the payload,
    # regardless of what the customer set DD_TRACE_PROPAGATION_STYLE_INJECT to.
    for forbidden in ("traceparent", "tracestate", "b3", "X-B3-TraceId"):
        assert forbidden not in headers, f"unexpected non-Datadog header {forbidden!r} in {headers!r}"


def test_suspend_during_checkpoint_save_does_not_mark_execute_span_error(test_spans):
    """SDK 3.x raises SuspendExecution from create_checkpoint when the service revokes
    the checkpoint token. The suspended invocation's execute span must not be an error.
    """
    from aws_durable_execution_sdk_python.exceptions import SuspendExecution

    @ades.durable_execution
    def workflow(event, context):
        context.wait(Duration.from_seconds(1), name="pause")

    with mock.patch.object(
        patch_module, "maybe_save_trace_context_checkpoint", side_effect=SuspendExecution("token revoked")
    ):
        with DurableFunctionTestRunner(workflow) as runner:
            runner.run()

    execute_spans = list(test_spans.filter_spans(name="aws.durable.execute"))
    assert execute_spans
    assert all(span.error == 0 for span in execute_spans)


def _callback_invocation_settled(runner, execution_arn, callback_id):
    """True once the invocation that created callback_id has completed.

    The SDK 3.x test runner waits for this before returning a callback; the 1.x
    runner does not, and completing a callback mid-invocation invalidates that
    invocation's checkpoint token.
    """
    # max_items: the 1.x runner returns 100 events per page by default.
    events = runner._executor.get_execution_history(execution_arn, max_items=10_000).events
    for index, event in enumerate(events):
        details = getattr(event, "callback_started_details", None)
        if event.event_type == "CallbackStarted" and details is not None and details.callback_id == callback_id:
            return any(later.event_type == "InvocationCompleted" for later in events[index + 1 :])
    return False


def _run_completing_callback(workflow):
    """Run a workflow with one callback, completing it once its invocation suspends."""
    with DurableFunctionTestRunner(workflow) as runner:
        execution_arn = runner.run_async()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            callback_id = runner.wait_for_callback(execution_arn, timeout=60)
            if _callback_invocation_settled(runner, execution_arn, callback_id):
                runner.send_callback_success(callback_id, result=b'"ok"')
                break
            time.sleep(0.1)
        runner.wait_for_result(execution_arn, timeout=60)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_wait_for_condition():
    """A condition polled twice: the first check suspends the workflow, the second
    stops polling, and the final invocation replays the completed operation.
    """

    def stop_on_second_attempt(state, attempt):
        if attempt >= 2:
            return WaitForConditionDecision.stop_polling()
        return WaitForConditionDecision.continue_waiting(Duration.from_seconds(1))

    @ades.durable_execution
    def workflow(event, context):
        context.wait_for_condition(
            lambda state, check_context: state + 1,
            config=WaitForConditionConfig(wait_strategy=stop_on_second_attempt, initial_state=0),
            name="poll",
        )
        context.wait(Duration.from_seconds(1), name="pause")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_run_in_child_context():
    """A child context and its step are traced, then replayed after the wait resumes."""

    @ades.durable_execution
    def workflow(event, context):
        context.run_in_child_context(lambda child: child.step(lambda _ctx: "ok", name="inner"), name="child")
        context.wait(Duration.from_seconds(1), name="pause")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()


def test_map_propagates_trace_context(test_spans):
    """context.map item steps inherit the trace_id and parent span_id from the map span
    across worker threads, and the map span keeps its own operation id.
    """
    map_operation_ids = []

    def process(item_ctx, item, index, items):
        return item_ctx.step(lambda _ctx: item, name=f"item-{index}")

    @ades.durable_execution
    def workflow(event, context):
        result = context.map([1, 2], process, name="items")
        map_operation_ids.extend(op.operation_id for op in context.state.operations.values() if op.name == "items")
        return result

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()

    map_span = test_spans.find_span(name=aws_durable.SPAN_MAP)
    assert map_span.get_tag(aws_durable.TAG_ID) == map_operation_ids[0]
    step_spans = list(test_spans.filter_spans(name=aws_durable.SPAN_STEP))
    assert sorted(span.get_tag(aws_durable.TAG_NAME) for span in step_spans) == ["item-0", "item-1"]
    for step_span in step_spans:
        assert step_span.trace_id == map_span.trace_id
        assert step_span.parent_id == map_span.span_id


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_create_callback():
    """create_callback is traced; waiting on its result suspends the workflow and the
    resume invocation replays the completed callback.
    """

    @ades.durable_execution
    def workflow(event, context):
        return context.create_callback(name="approval").result()

    _run_completing_callback(workflow)


# SDK 3.x runs wait_for_callback's body in a private child context: its operations are
# direct children of the wait_for_callback span instead of an aws.durable.child_context span.
@pytest.mark.snapshot(
    ignores=SNAPSHOT_IGNORES,
    token="tests.contrib.aws_durable_execution_sdk_python.test_aws_durable_execution_sdk_python.test_wait_for_callback"
    + ("_sdk3" if SDK_VERSION >= (3, 0, 0) else ""),
)
def test_wait_for_callback():
    """wait_for_callback and its submitter are traced, then replayed once the callback completes."""

    def submitter(callback_id, callback_context):
        pass

    @ades.durable_execution
    def workflow(event, context):
        return context.wait_for_callback(submitter, name="approval")

    _run_completing_callback(workflow)


@pytest.mark.skipif(SDK_VERSION < (3, 0, 0), reason="the SDK 1.x test runner cannot resolve chained-invoke targets")
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_invoke():
    """context.invoke is traced with the target function name, and replayed once the target completes."""

    @ades.durable_execution
    def workflow(event, context):
        return context.invoke("target-function", {"x": 1}, name="call")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.register_function("target-function", lambda payload, lambda_context: payload)
        runner.run()


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


def test_durable_context_parent_id_attribute_contract():
    """Snitch test: the integration reads ``durable_context._parent_id`` to
    parent ``_datadog_*`` checkpoints inside ``run_in_child_context``.  Our
    ``getattr(..., None)`` would silently return ``None`` if the SDK ever
    renamed or dropped the attribute — AWS would still accept the checkpoint
    (None = child of execution), so the bug would only surface as wrong
    parenting in the durable trace tree, not as a crash.

    Pins the SDK contract:
      - Top-level ``_parent_id`` is ``None``.
      - Inside ``run_in_child_context``, ``_parent_id`` equals the parent
        operation id (a non-empty ``str``) — which is the value our
        integration stamps onto ``OperationIdentifier.parent_id``.
    """
    observed: dict = {}

    def child_body(child_ctx):
        observed["child_parent_id"] = getattr(child_ctx, "_parent_id", "<missing>")
        return "done"

    @ades.durable_execution
    def workflow(event, context):
        observed["top_parent_id"] = getattr(context, "_parent_id", "<missing>")
        context.run_in_child_context(child_body, name="child")
        # Record the child op's id so we verify ``_parent_id`` really is the
        # parent operation id (the semantic our integration depends on),
        # not just some opaque non-empty string.
        for op in context.state.operations.values():
            if getattr(op, "name", None) == "child":
                observed["child_op_id"] = op.operation_id
                break

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()

    assert observed.get("top_parent_id") is None, (
        f"Top-level DurableContext._parent_id must be None, got "
        f"{observed.get('top_parent_id')!r}. Did the SDK rename _parent_id?"
    )
    child_pid = observed.get("child_parent_id")
    assert isinstance(child_pid, str) and child_pid, (
        f"Child DurableContext._parent_id must be a non-empty str (the "
        f"parent operation id), got {child_pid!r}. Did the SDK rename _parent_id?"
    )
    assert child_pid == observed.get("child_op_id"), (
        f"Child _parent_id ({child_pid!r}) must equal the parent operation "
        f"id ({observed.get('child_op_id')!r}); the integration stamps this "
        f"onto OperationIdentifier.parent_id."
    )


# SDK 3.x raises StepError instead of CallableRuntimeError when a step exhausts its
# retries, so the execute span's error.type differs by version.
@pytest.mark.snapshot(
    ignores=SNAPSHOT_IGNORES,
    token="tests.contrib.aws_durable_execution_sdk_python.test_aws_durable_execution_sdk_python.test_workflow_failed_status"
    + ("_sdk3" if SDK_VERSION >= (3, 0, 0) else ""),
)
def test_workflow_failed_status():
    """Step that exhausts retries: terminal aws.durable.execute span has invocation_status=failed."""

    def always_fails(step_context):
        raise RuntimeError("permanent failure")

    @ades.durable_execution
    def workflow(event, context):
        return context.step(always_fails, name="always-fails", config=_fast_retry(max_attempts=1))

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()


def test_step_runs_when_current_context_has_no_event(test_spans):
    """A step started while the current core context carries no event must still run
    instead of failing with "ExecutionContext has no event" (seen in warm Lambda containers).
    """
    calls = []

    def body(step_context):
        calls.append(True)
        return "ok"

    @ades.durable_execution
    def workflow(event, context):
        # A context created without an event, like the one a later execution in a
        # warm container can find itself under.
        with core.context_with_data("test.no_event"):
            return context.step(body, name="no-event-step")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()

    assert calls == [True]
    step_span = test_spans.find_span(name=aws_durable.SPAN_STEP)
    assert step_span.resource == "no-event-step"


def test_event_readers_tolerate_missing_event():
    """The helpers that read the active core event treat "no event" as "no parent"."""
    with core.context_with_data("test.no_event"):
        assert _is_dynamic_parent() is False

        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            error = e
        assert _traced_retry_handler(lambda err: "handled", None, (error,), {}) == "handled"
