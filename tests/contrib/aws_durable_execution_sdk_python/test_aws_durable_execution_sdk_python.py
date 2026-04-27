"""Integration tests for the aws_durable_execution_sdk_python integration.

Driven by the upstream ``aws-durable-execution-sdk-python-testing`` in-process
runner, which executes a real ``@durable_execution`` handler end-to-end and
naturally exercises the SDK's suspend / replay / retry control flow.

Assertions are done via ``@pytest.mark.snapshot`` against the local test
agent.
"""

from unittest import mock

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
    from aws_durable_execution_sdk_python.config import Duration
    from aws_durable_execution_sdk_python.config import StepConfig
    from aws_durable_execution_sdk_python.retries import RetryStrategyConfig
    from aws_durable_execution_sdk_python.retries import create_retry_strategy

    return StepConfig(
        retry_strategy=create_retry_strategy(
            RetryStrategyConfig(max_attempts=max_attempts, initial_delay=Duration.from_seconds(1))
        )
    )


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_durable_execution():
    """Happy-path workflow with no SDK operations: a single aws.durable.execute span."""
    from aws_durable_execution_sdk_python import durable_execution
    from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner

    @durable_execution
    def workflow(event, context):
        return {"ok": True}

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run(input='{"hello": "world"}')


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_step_with_retry():
    """Step that fails on attempt 1 and succeeds on attempt 2."""
    from aws_durable_execution_sdk_python import durable_execution
    from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner

    attempts = {"n": 0}

    def flaky(step_context):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient failure")
        return "ok"

    @durable_execution
    def workflow(event, context):
        return context.step(flaky, name="flaky", config=_fast_retry(max_attempts=2))

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_step_replayed():
    """Single successful step. Runner drives one suspending invocation followed by a
    replay invocation; the replay's step span carries aws.durable.replayed=true.
    """
    from aws_durable_execution_sdk_python import durable_execution
    from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner

    def compute(step_context):
        return 42

    @durable_execution
    def workflow(event, context):
        return context.step(compute, name="compute")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_parallel_propagates_trace_context():
    """context.parallel uses TracedThreadPoolExecutor so child step spans inherit the
    trace_id and parent span_id from the parallel span across worker threads.
    """
    from aws_durable_execution_sdk_python import durable_execution
    from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner

    def work(step_context):
        return "done"

    def branch_a(child_ctx):
        return child_ctx.step(work, name="a")

    def branch_b(child_ctx):
        return child_ctx.step(work, name="b")

    @durable_execution
    def workflow(event, context):
        return context.parallel([branch_a, branch_b], name="fan-out")

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_invoke_tags():
    """invoke span carries function_name and span.kind=client.

    The InProcessInvoker is shared between the workflow's own invocation and the
    chained invoke. We stub only the chained call to ``my-target-fn`` so the
    workflow handler still runs.
    """
    from aws_durable_execution_sdk_python import durable_execution
    from aws_durable_execution_sdk_python.execution import DurableExecutionInvocationOutput
    from aws_durable_execution_sdk_python.execution import InvocationStatus
    from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner
    from aws_durable_execution_sdk_python_testing.invoker import InProcessInvoker
    from aws_durable_execution_sdk_python_testing.invoker import InvokeResponse

    fake_output = DurableExecutionInvocationOutput(status=InvocationStatus.SUCCEEDED, result='"downstream-result"')
    fake_response = InvokeResponse(invocation_output=fake_output, request_id="fake-request-id")
    real_invoke = InProcessInvoker.invoke

    def fake_invoke(self, function_name, input_, endpoint_url=None):
        if function_name == "my-target-fn":
            return fake_response
        return real_invoke(self, function_name, input_, endpoint_url=endpoint_url)

    @durable_execution
    def workflow(event, context):
        return context.invoke(function_name="my-target-fn", payload=b"{}", name="call-downstream")

    with mock.patch.object(InProcessInvoker, "invoke", new=fake_invoke):
        with DurableFunctionTestRunner(workflow) as runner:
            runner.run()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_workflow_failed_status():
    """Step that exhausts retries: terminal aws.durable.execute span has invocation_status=failed."""
    from aws_durable_execution_sdk_python import durable_execution
    from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner

    def always_fails(step_context):
        raise RuntimeError("permanent failure")

    @durable_execution
    def workflow(event, context):
        return context.step(always_fails, name="always-fails", config=_fast_retry(max_attempts=1))

    with DurableFunctionTestRunner(workflow) as runner:
        runner.run()
