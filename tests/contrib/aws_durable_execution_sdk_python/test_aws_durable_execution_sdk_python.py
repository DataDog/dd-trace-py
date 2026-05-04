import aws_durable_execution_sdk_python as ades
from aws_durable_execution_sdk_python.config import Duration
from aws_durable_execution_sdk_python.config import StepConfig
from aws_durable_execution_sdk_python.retries import RetryStrategyConfig
from aws_durable_execution_sdk_python.retries import create_retry_strategy
from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner
import pytest

from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import patch
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import unpatch


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
def test_durable_execution():
    """Happy-path workflow with no SDK operations: a single aws.durable.execute span."""

    @ades.durable_execution
    def workflow(event, context):
        return {"ok": True}

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run(input='{"hello": "world"}')


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
