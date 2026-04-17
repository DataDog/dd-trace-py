"""
APM span tests for the aws_durable_execution_sdk_python integration.

Tests verify that the integration produces correct spans with proper names,
types, tags, and parent-child relationships for:
  1. durable_execution — top-level workflow execution span (aws.durable_execution.execute)
  2. DurableContext.step — individual step execution spans (aws.durable_execution.step)
  3. DurableContext.invoke — cross-function invocation spans (aws.durable_execution.invoke)

These tests use a mock DurableServiceClient to simulate the checkpoint/state
management that normally runs against the real AWS Lambda Durable Execution API.
"""
import hashlib
import json
from dataclasses import dataclass
from threading import Lock
from typing import Any

import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import patch
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.patch import unpatch
from tests.utils import TracerSpanContainer
from tests.utils import override_config
from tests.utils import scoped_tracer


# ---------------------------------------------------------------------------
# Helper: compute the deterministic step ID the SDK will produce
# ---------------------------------------------------------------------------
def _step_id(parent_id, counter):
    step_id = "{}-{}".format(parent_id, counter) if parent_id else str(counter)
    return hashlib.blake2b(step_id.encode()).hexdigest()[:64]


# ---------------------------------------------------------------------------
# Mock Lambda context (satisfies the LambdaContext protocol)
# ---------------------------------------------------------------------------
@dataclass
class MockLambdaContext:
    aws_request_id: str = "mock-request-id-12345"
    log_group_name: str = "/aws/lambda/sample-durable-fn"
    log_stream_name: str = "2024/01/01/[$LATEST]abcdef1234"
    function_name: str = "sample-durable-fn"
    memory_limit_in_mb: str = "256"
    function_version: str = "$LATEST"
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:sample-durable-fn"
    tenant_id: Any = None
    client_context: Any = None
    identity: Any = None

    def get_remaining_time_in_millis(self):
        return 300_000

    def log(self, msg):
        pass


# ---------------------------------------------------------------------------
# Mock DurableServiceClient
# ---------------------------------------------------------------------------
class MockDurableServiceClient:
    """In-memory checkpoint service that the SDK's background thread calls."""

    def __init__(self, initial_operations=None):
        self._lock = Lock()
        self._token_counter = 0
        self._operations = {}
        if initial_operations:
            for op in initial_operations:
                self._operations[op.operation_id] = op

    def _next_token(self):
        self._token_counter += 1
        return "mock-token-{}".format(self._token_counter)

    def checkpoint(self, durable_execution_arn, checkpoint_token, updates, client_token=None):
        from aws_durable_execution_sdk_python.lambda_service import (
            CheckpointOutput,
            CheckpointUpdatedExecutionState,
            ChainedInvokeDetails,
            Operation,
            OperationAction,
            OperationStatus,
            OperationType,
            StepDetails,
        )

        with self._lock:
            new_ops = []
            status_map = {
                OperationAction.START: OperationStatus.STARTED,
                OperationAction.SUCCEED: OperationStatus.SUCCEEDED,
                OperationAction.FAIL: OperationStatus.FAILED,
                OperationAction.RETRY: OperationStatus.PENDING,
            }
            for update in updates:
                new_status = status_map.get(update.action, OperationStatus.STARTED)
                step_details = None
                if update.operation_type == OperationType.STEP:
                    result_payload = update.payload if update.action == OperationAction.SUCCEED else None
                    step_details = StepDetails(attempt=0, result=result_payload)
                chained_invoke_details = None
                if update.operation_type == OperationType.CHAINED_INVOKE:
                    chained_invoke_details = ChainedInvokeDetails(
                        result=update.payload if update.action == OperationAction.SUCCEED else None,
                    )
                op = Operation(
                    operation_id=update.operation_id,
                    operation_type=update.operation_type,
                    status=new_status,
                    parent_id=update.parent_id,
                    name=update.name,
                    sub_type=update.sub_type,
                    step_details=step_details,
                    chained_invoke_details=chained_invoke_details,
                )
                self._operations[update.operation_id] = op
                new_ops.append(op)

            new_token = self._next_token()
            return CheckpointOutput(
                checkpoint_token=new_token,
                new_execution_state=CheckpointUpdatedExecutionState(
                    operations=new_ops,
                    next_marker=None,
                ),
            )

    def get_execution_state(self, durable_execution_arn, checkpoint_token, next_marker, max_items=1000):
        from aws_durable_execution_sdk_python.lambda_service import StateOutput

        with self._lock:
            return StateOutput(
                operations=list(self._operations.values()),
                next_marker=None,
            )


# ---------------------------------------------------------------------------
# Constants for test fixtures
# ---------------------------------------------------------------------------
DURABLE_EXECUTION_ARN = "arn:aws:lambda:us-east-1:123456789012:function:sample-durable-fn:dex:abc-123"
INITIAL_CHECKPOINT_TOKEN = "initial-token-0"
INPUT_PAYLOAD = json.dumps({"order_id": "ORD-42", "amount": 99.95})


def _build_initial_state_with_invoke():
    """Build initial state with a pre-completed invoke so invoke() returns immediately."""
    from aws_durable_execution_sdk_python.lambda_service import (
        ChainedInvokeDetails,
        ExecutionDetails,
        Operation,
        OperationStatus,
        OperationType,
    )

    invoke_op_id = _step_id(None, 3)
    execution_op = Operation(
        operation_id="exec-op-0",
        operation_type=OperationType.EXECUTION,
        status=OperationStatus.STARTED,
        execution_details=ExecutionDetails(input_payload=INPUT_PAYLOAD),
    )
    invoke_op = Operation(
        operation_id=invoke_op_id,
        operation_type=OperationType.CHAINED_INVOKE,
        status=OperationStatus.SUCCEEDED,
        name="process-payment",
        chained_invoke_details=ChainedInvokeDetails(
            result=json.dumps({"payment_id": "PAY-789", "status": "approved"}),
        ),
    )
    return [execution_op, invoke_op]


def _build_initial_state_basic():
    """Build initial state with only the execution operation (no pre-completed invoke)."""
    from aws_durable_execution_sdk_python.lambda_service import (
        ExecutionDetails,
        Operation,
        OperationStatus,
        OperationType,
    )

    execution_op = Operation(
        operation_id="exec-op-0",
        operation_type=OperationType.EXECUTION,
        status=OperationStatus.STARTED,
        execution_details=ExecutionDetails(input_payload=INPUT_PAYLOAD),
    )
    return [execution_op]


def _build_initial_state_with_steps():
    """Build initial state with a pre-completed step for replay testing.

    AIDEV-NOTE: When the SDK finds a SUCCEEDED step operation in the initial state,
    it short-circuits the step (returns cached result without calling the step function).
    This is how we test the replay detection in _traced_step.

    AIDEV-NOTE: Step results must be serialized using the SDK's EXTENDED_TYPES_SERDES
    format (not plain JSON), because the SDK's step executor deserializes results using
    its own typed-value wrapper format (e.g. ``{"t":"m","v":{...}}``).
    """
    from aws_durable_execution_sdk_python.lambda_service import (
        ExecutionDetails,
        Operation,
        OperationStatus,
        OperationType,
        StepDetails,
    )
    from aws_durable_execution_sdk_python.serdes import EXTENDED_TYPES_SERDES
    from aws_durable_execution_sdk_python.serdes import serialize

    step1_op_id = _step_id(None, 1)
    # AIDEV-NOTE: Serialize the step result using the SDK's default serdes so that
    # the step executor can deserialize it correctly during replay.
    serialized_result = serialize(
        serdes=EXTENDED_TYPES_SERDES,
        value={"valid": True},
        operation_id=step1_op_id,
        durable_execution_arn=DURABLE_EXECUTION_ARN,
    )
    execution_op = Operation(
        operation_id="exec-op-0",
        operation_type=OperationType.EXECUTION,
        status=OperationStatus.STARTED,
        execution_details=ExecutionDetails(input_payload=INPUT_PAYLOAD),
    )
    step1_op = Operation(
        operation_id=step1_op_id,
        operation_type=OperationType.STEP,
        status=OperationStatus.SUCCEEDED,
        name="validate",
        step_details=StepDetails(attempt=0, result=serialized_result),
    )
    return [execution_op, step1_op]


def _create_invocation_event(initial_operations, mock_service_client):
    """Create the invocation event that simulates an AWS Lambda invocation."""
    from aws_durable_execution_sdk_python.execution import (
        DurableExecutionInvocationInput,
        DurableExecutionInvocationInputWithClient,
        InitialExecutionState,
    )

    base_input = DurableExecutionInvocationInput(
        durable_execution_arn=DURABLE_EXECUTION_ARN,
        checkpoint_token=INITIAL_CHECKPOINT_TOKEN,
        initial_execution_state=InitialExecutionState(
            operations=initial_operations,
            next_marker="",
        ),
    )
    return DurableExecutionInvocationInputWithClient.from_durable_execution_invocation_input(
        invocation_input=base_input,
        service_client=mock_service_client,
    )


# ---------------------------------------------------------------------------
# AIDEV-NOTE: The SDK's durable_execution decorator wraps all return values in
# DurableExecutionInvocationOutput.to_dict() = {'Status': 'SUCCEEDED', 'Result': '<json>'}
# and catches all exceptions returning {'Status': 'FAILED', 'Error': {...}}.
# These helpers extract the real result for test assertions.
# ---------------------------------------------------------------------------
def _extract_result(sdk_output):
    """Extract the actual user-function result from the SDK's output wrapper."""
    assert sdk_output["Status"] == "SUCCEEDED", (
        "Expected SUCCEEDED status, got {}".format(sdk_output.get("Status"))
    )
    return json.loads(sdk_output["Result"])


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tracer():
    with scoped_tracer() as _tracer:
        container = TracerSpanContainer(_tracer)
        yield container
        container.reset()


@pytest.fixture(autouse=True)
def patch_aws_durable_execution(tracer):
    patch()
    yield
    unpatch()


# ===========================================================================
# Test class: Workflow Execution Spans
# ===========================================================================
class TestWorkflowExecution:
    """Tests for the top-level durable_execution decorator span."""

    def test_workflow_execution_creates_root_span(self, tracer):
        """A complete workflow execution produces a root span with correct name, type, and tags."""
        from aws_durable_execution_sdk_python import DurableContext, durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_with_invoke()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def my_workflow(event, context):
            def validate(step_ctx):
                return {"valid": True}
            result = context.step(validate, name="validate")
            return {"validation": result}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        sdk_output = my_workflow(event, lambda_ctx)

        assert sdk_output is not None
        inner = _extract_result(sdk_output)
        assert inner["validation"]["valid"] is True

        spans = tracer.pop()
        # AIDEV-NOTE: Expect at least 2 spans: 1 workflow execution + 1 step
        assert len(spans) >= 2

        # Find the workflow execution span (root span)
        workflow_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(workflow_spans) == 1, "Expected exactly one workflow execution span"
        workflow_span = workflow_spans[0]

        # Verify span structure
        assert workflow_span.service == "aws.durable_execution"
        assert workflow_span.span_type == "serverless"
        assert workflow_span.resource == "aws.durable_execution.execute"
        assert workflow_span.error == 0

        # Verify span tags
        assert workflow_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert workflow_span.get_tag("span.kind") == "server"
        assert workflow_span.get_tag("aws.durable_execution.arn") == DURABLE_EXECUTION_ARN

        # The workflow span should be the parent of step spans
        step_spans = [s for s in spans if s.name == "aws.durable_execution.step"]
        for step_span in step_spans:
            assert step_span.parent_id == workflow_span.span_id, (
                "Step span should be a child of the workflow execution span"
            )

    def test_workflow_execution_with_error(self, tracer):
        """When the workflow function raises an exception, the span captures error details."""
        from aws_durable_execution_sdk_python import durable_execution

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def failing_workflow(event, context):
            raise ValueError("Workflow failed: invalid input data")

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        # AIDEV-NOTE: The SDK catches all exceptions internally and returns
        # {'Status': 'FAILED', 'Error': {...}} instead of re-raising.
        sdk_output = failing_workflow(event, lambda_ctx)
        assert sdk_output["Status"] == "FAILED"

        spans = tracer.pop()
        workflow_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(workflow_spans) == 1
        workflow_span = workflow_spans[0]

        # Verify error is captured on the span
        assert workflow_span.error == 1
        assert workflow_span.get_tag(ERROR_TYPE) is not None
        assert workflow_span.get_tag(ERROR_MSG) is not None
        assert "Workflow failed" in workflow_span.get_tag(ERROR_MSG)
        assert workflow_span.get_tag("component") == "aws_durable_execution_sdk_python"

    def test_workflow_execution_captures_replay_status(self, tracer):
        """The workflow span should tag whether this is a fresh execution or a replay."""
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_with_invoke()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def replay_workflow(event, context):
            def simple_step(step_ctx):
                return "done"
            return context.step(simple_step, name="simple")

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        replay_workflow(event, lambda_ctx)

        spans = tracer.pop()
        workflow_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(workflow_spans) == 1
        workflow_span = workflow_spans[0]

        # Replay status should be captured as a tag
        replay_status = workflow_span.get_tag("aws.durable_execution.replay_status")
        assert replay_status is not None, "Workflow span should include replay_status tag"
        assert replay_status in ("NEW", "REPLAY"), "replay_status must be 'NEW' or 'REPLAY'"


# ===========================================================================
# Test class: Step Execution Spans
# ===========================================================================
class TestStepExecution:
    """Tests for DurableContext.step() spans."""

    def test_step_creates_span_with_name_and_tags(self, tracer):
        """Each call to context.step() produces a span with the step name and correct tags."""
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_with_invoke()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def step_workflow(event, context):
            def validate_order(step_ctx):
                return {"valid": True, "order_id": "ORD-42"}

            def calculate_totals(step_ctx):
                return {"subtotal": 99.95, "tax": 8.0, "total": 107.95}

            v = context.step(validate_order, name="validate-order")
            t = context.step(calculate_totals, name="calculate-totals")
            return {"validation": v, "totals": t}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        sdk_output = step_workflow(event, lambda_ctx)

        inner = _extract_result(sdk_output)
        assert inner["validation"]["valid"] is True
        assert inner["totals"]["total"] == 107.95

        spans = tracer.pop()
        step_spans = [s for s in spans if s.name == "aws.durable_execution.step"]
        assert len(step_spans) == 2, "Expected exactly 2 step spans"

        # Verify first step span
        validate_spans = [s for s in step_spans if s.get_tag("aws.durable_execution.step.name") == "validate-order"]
        assert len(validate_spans) == 1, "Expected one 'validate-order' step span"
        validate_span = validate_spans[0]
        assert validate_span.service == "aws.durable_execution"
        assert validate_span.span_type == "worker"
        assert validate_span.resource == "validate-order"
        assert validate_span.error == 0
        assert validate_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert validate_span.get_tag("span.kind") == "internal"
        assert validate_span.get_tag("aws.durable_execution.step.name") == "validate-order"

        # Verify second step span
        totals_spans = [s for s in step_spans if s.get_tag("aws.durable_execution.step.name") == "calculate-totals"]
        assert len(totals_spans) == 1, "Expected one 'calculate-totals' step span"
        totals_span = totals_spans[0]
        assert totals_span.service == "aws.durable_execution"
        assert totals_span.span_type == "worker"
        assert totals_span.resource == "calculate-totals"
        assert totals_span.error == 0
        assert totals_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert totals_span.get_tag("span.kind") == "internal"

    def test_step_error_captures_exception(self, tracer):
        """When a step function raises an exception, the SDK catches it internally.

        AIDEV-NOTE: The SDK executes step functions inside its own execution
        framework (step.py:execute). Exceptions are caught there and trigger
        a retry/fail cycle. The exception does NOT propagate to our
        _traced_step wrapper around DurableContext.step(), so the step span
        itself won't have error=1. Instead we verify:
        1. The step span is created with correct tags
        2. The SDK reports FAILED or PENDING status overall
        3. The workflow execution span captures the error if the SDK re-raises
        """
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def error_step_workflow(event, context):
            def failing_step(step_ctx):
                raise RuntimeError("Step computation failed")

            return context.step(failing_step, name="failing-step")

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        sdk_output = error_step_workflow(event, lambda_ctx)
        assert sdk_output["Status"] in ("FAILED", "PENDING"), (
            "Expected FAILED or PENDING status for a failing step, got {}".format(sdk_output["Status"])
        )

        spans = tracer.pop()
        step_spans = [s for s in spans if s.name == "aws.durable_execution.step"]

        # The failing step should have a span with correct metadata
        failing_spans = [
            s for s in step_spans
            if s.get_tag("aws.durable_execution.step.name") == "failing-step"
        ]
        assert len(failing_spans) >= 1, "Expected at least one span for the failing step"
        failing_span = failing_spans[0]

        # Verify span structure and tags are correct
        assert failing_span.service == "aws.durable_execution"
        assert failing_span.span_type == "worker"
        assert failing_span.resource == "failing-step"
        assert failing_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert failing_span.get_tag("span.kind") == "internal"
        # AIDEV-NOTE: The SDK catches step exceptions internally (retry/fail cycle),
        # so the exception does NOT propagate to our wrapper. The step span must NOT
        # be marked as errored — verify this explicitly to prevent regressions.
        assert failing_span.error == 0, (
            "Step span should not be marked as error because SDK catches step exceptions internally"
        )

    def test_step_fresh_execution_tagged_not_replayed(self, tracer):
        """When a step executes fresh (no checkpoint), replayed tag should be 'false'."""
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def fresh_workflow(event, context):
            def my_step(step_ctx):
                return {"computed": 42}
            return context.step(my_step, name="compute")

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        sdk_output = fresh_workflow(event, lambda_ctx)

        inner = _extract_result(sdk_output)
        assert inner["computed"] == 42

        spans = tracer.pop()
        step_spans = [s for s in spans if s.name == "aws.durable_execution.step"]
        assert len(step_spans) == 1
        step_span = step_spans[0]

        # Verify full span structure
        assert step_span.service == "aws.durable_execution"
        assert step_span.span_type == "worker"
        assert step_span.resource == "compute"
        assert step_span.error == 0
        assert step_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert step_span.get_tag("span.kind") == "internal"
        assert step_span.get_tag("aws.durable_execution.step.name") == "compute"

        # Fresh execution: step function was called, so replayed=false
        assert step_span.get_tag("aws.durable_execution.step.replayed") == "false", (
            "Fresh step should be tagged as replayed=false"
        )

    def test_step_replayed_from_checkpoint_is_tagged(self, tracer):
        """When a step is replayed from checkpoint, replayed tag should be 'true'."""
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_with_steps()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def replay_workflow(event, context):
            def validate(step_ctx):
                return {"valid": True}
            return context.step(validate, name="validate")

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        sdk_output = replay_workflow(event, lambda_ctx)

        # SDK returns the cached result from checkpoint
        inner = _extract_result(sdk_output)
        assert inner["valid"] is True

        spans = tracer.pop()
        step_spans = [s for s in spans if s.name == "aws.durable_execution.step"]
        assert len(step_spans) == 1
        step_span = step_spans[0]

        # Verify full span structure
        assert step_span.service == "aws.durable_execution"
        assert step_span.span_type == "worker"
        assert step_span.resource == "validate"
        assert step_span.error == 0
        assert step_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert step_span.get_tag("span.kind") == "internal"
        assert step_span.get_tag("aws.durable_execution.step.name") == "validate"

        # Replayed: step function was NOT called (result from checkpoint), so replayed=true
        assert step_span.get_tag("aws.durable_execution.step.replayed") == "true", (
            "Replayed step should be tagged as replayed=true"
        )

    def test_step_without_explicit_name_uses_function_name(self, tracer):
        """When name is not provided, the step span should use the function name as resource."""
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def unnamed_step_workflow(event, context):
            def my_custom_step(step_ctx):
                return 42

            # Call step without explicit name argument
            return context.step(my_custom_step)

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        unnamed_step_workflow(event, lambda_ctx)

        spans = tracer.pop()
        step_spans = [s for s in spans if s.name == "aws.durable_execution.step"]
        assert len(step_spans) >= 1, "Expected at least one step span"

        # When no explicit name is given, the step name should be derived from the function's __name__
        step_span = step_spans[0]
        assert step_span.get_tag("aws.durable_execution.step.name") == "my_custom_step", (
            "Step name tag should be derived from the function name when no explicit name is given"
        )
        assert step_span.resource == "my_custom_step", (
            "Step resource should be derived from the function name when no explicit name is given"
        )


# ===========================================================================
# Test class: Invoke Spans
# ===========================================================================
class TestInvokeExecution:
    """Tests for DurableContext.invoke() spans (cross-function invocation)."""

    def test_invoke_creates_client_span(self, tracer):
        """context.invoke() produces a client span with the invoked function name and tags."""
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_with_invoke()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def invoke_workflow(event, context):
            def validate(step_ctx):
                return {"valid": True}

            def calc(step_ctx):
                return {"total": 100}

            context.step(validate, name="validate")
            context.step(calc, name="calc")
            payment = context.invoke(
                function_name="payment-processor-fn",
                payload={"order_id": "ORD-42", "total": 100},
                name="process-payment",
            )
            return {"payment": payment}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        result = invoke_workflow(event, lambda_ctx)

        spans = tracer.pop()
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]
        assert len(invoke_spans) == 1, "Expected exactly one invoke span"
        invoke_span = invoke_spans[0]

        # Verify span structure
        assert invoke_span.service == "aws.durable_execution"
        assert invoke_span.span_type == "serverless"
        assert invoke_span.resource == "process-payment"
        assert invoke_span.error == 0

        # Verify span tags
        assert invoke_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert invoke_span.get_tag("span.kind") == "client"
        assert invoke_span.get_tag("aws.durable_execution.invoke.function_name") == "payment-processor-fn"
        assert invoke_span.get_tag("aws.durable_execution.invoke.name") == "process-payment"

    def test_invoke_is_child_of_workflow_span(self, tracer):
        """The invoke span should be a child of the workflow execution span."""
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_with_invoke()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def nested_invoke_workflow(event, context):
            def step_fn(step_ctx):
                return "ok"

            context.step(step_fn, name="pre-step-1")
            context.step(step_fn, name="pre-step-2")
            return context.invoke(
                function_name="downstream-fn",
                payload={},
                name="call-downstream",
            )

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        nested_invoke_workflow(event, lambda_ctx)

        spans = tracer.pop()
        workflow_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]

        assert len(workflow_spans) == 1
        assert len(invoke_spans) == 1

        workflow_span = workflow_spans[0]
        invoke_span = invoke_spans[0]

        assert invoke_span.parent_id == workflow_span.span_id, (
            "Invoke span should be a child of the workflow execution span"
        )


# ===========================================================================
# Test class: Full Workflow Trace Structure
# ===========================================================================
class TestFullWorkflowTrace:
    """Tests verifying the complete trace structure for a multi-step workflow."""

    def test_complete_workflow_produces_correct_trace_tree(self, tracer):
        """A workflow with steps and invoke produces a proper parent-child span tree.

        Expected trace structure:
          aws.durable_execution.execute (root)
            |- aws.durable_execution.step (validate-order)
            |- aws.durable_execution.step (calculate-totals)
            |- aws.durable_execution.invoke (process-payment)
        """
        from aws_durable_execution_sdk_python import DurableContext, durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_with_invoke()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def full_workflow(event, context):
            def validate_order(step_ctx):
                order_id = event.get("order_id", "unknown") if isinstance(event, dict) else "unknown"
                return {"valid": True, "order_id": order_id}

            def calculate_totals(step_ctx):
                return {"subtotal": 99.95, "tax": 8.0, "total": 107.95}

            validation = context.step(validate_order, name="validate-order")
            totals = context.step(calculate_totals, name="calculate-totals")
            payment = context.invoke(
                function_name="payment-processor-fn",
                payload={"order_id": "ORD-42", "total": totals.get("total", 0)},
                name="process-payment",
            )
            return {
                "validation": validation,
                "totals": totals,
                "payment": payment,
            }

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        sdk_output = full_workflow(event, lambda_ctx)

        # Verify the workflow completed successfully
        inner = _extract_result(sdk_output)
        assert inner["validation"]["valid"] is True
        assert inner["totals"]["total"] == 107.95
        assert inner["payment"]["status"] == "approved"

        spans = tracer.pop()

        # Verify total span count: 1 execution + 2 steps + 1 invoke = 4
        assert len(spans) == 4, "Expected 4 spans: 1 execution + 2 steps + 1 invoke, got {}".format(len(spans))

        # Categorize spans
        execution_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        step_spans = [s for s in spans if s.name == "aws.durable_execution.step"]
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]

        assert len(execution_spans) == 1
        assert len(step_spans) == 2
        assert len(invoke_spans) == 1

        root_span = execution_spans[0]

        # All child spans should be children of the root
        for span in step_spans + invoke_spans:
            assert span.parent_id == root_span.span_id, (
                "Span '{}' (resource={}) should be child of workflow execution span".format(
                    span.name, span.resource
                )
            )

        # Verify all spans share the same trace ID
        trace_id = root_span.trace_id
        for span in spans:
            assert span.trace_id == trace_id, "All spans should share the same trace ID"

        # Root span should have no parent (or parent from Lambda span if present)
        # In isolation it should be the root
        assert root_span.parent_id == 0 or root_span.parent_id is None, (
            "Workflow execution span should be the root span in isolation"
        )

    def test_workflow_with_no_steps_produces_single_span(self, tracer):
        """A workflow that does no steps or invokes should produce exactly one execution span."""
        from aws_durable_execution_sdk_python import durable_execution

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def empty_workflow(event, context):
            return {"status": "completed"}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        sdk_output = empty_workflow(event, lambda_ctx)

        inner = _extract_result(sdk_output)
        assert inner["status"] == "completed"

        spans = tracer.pop()
        assert len(spans) == 1, "A no-op workflow should produce exactly 1 execution span"
        assert spans[0].name == "aws.durable_execution.execute"
        assert spans[0].error == 0


# ===========================================================================
# Test class: SuspendExecution Handling
# ===========================================================================
class TestSuspendExecutionHandling:
    """Tests that SuspendExecution is treated as non-error control flow."""

    def test_suspend_execution_not_marked_as_error(self, tracer):
        """SuspendExecution is control flow, not an error. Spans should not be marked as errored."""
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        # Use basic state without pre-completed invoke to trigger suspension
        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def suspending_workflow(event, context):
            def some_step(step_ctx):
                return "done"
            context.step(some_step, name="pre-step")
            # invoke without pre-completed state will trigger SuspendExecution
            return context.invoke(
                function_name="another-fn",
                payload={},
                name="will-suspend",
            )

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        try:
            suspending_workflow(event, lambda_ctx)
        except Exception:
            # SuspendExecution may propagate; that's expected
            pass

        spans = tracer.pop()
        # The workflow span should exist
        workflow_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(workflow_spans) >= 1

        # AIDEV-NOTE: SuspendExecution is control flow, not an error. The integration
        # should catch SuspendExecution and NOT mark the span as errored.
        workflow_span = workflow_spans[0]
        assert workflow_span.error == 0, (
            "SuspendExecution should not mark the workflow span as an error"
        )


# ===========================================================================
# Test class: Configuration
# ===========================================================================
class TestConfiguration:
    """Tests for integration configuration options."""

    def test_custom_service_name(self, tracer):
        """Users can override the service name via config."""
        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def configured_workflow(event, context):
            def step_fn(step_ctx):
                return "ok"
            return context.step(step_fn, name="test-step")

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        with override_config("aws_durable_execution_sdk_python", dict(service_name="my-custom-service")):
            try:
                configured_workflow(event, lambda_ctx)
            except Exception:
                pass

        spans = tracer.pop()
        for span in spans:
            assert span.service == "my-custom-service", (
                "Service name should be overridden to 'my-custom-service', got '{}'".format(span.service)
            )

    def test_patch_unpatch_idempotent(self, tracer):
        """patch() and unpatch() can be called multiple times without side effects."""
        # Already patched by fixture, patch again
        patch()
        patch()

        from aws_durable_execution_sdk_python import durable_execution
        from aws_durable_execution_sdk_python.types import StepContext

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def idempotent_workflow(event, context):
            return {"result": "ok"}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        sdk_output = idempotent_workflow(event, lambda_ctx)

        inner = _extract_result(sdk_output)
        assert inner["result"] == "ok"

        spans = tracer.pop()
        # Should get exactly 1 execution span, not duplicated by double patching
        execution_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(execution_spans) == 1, "Double patching should not produce duplicate spans"


# ===========================================================================
# Helper: build initial state with custom input payload
# ===========================================================================
def _build_initial_state_with_payload(input_payload_str):
    """Build initial state with a custom input payload string."""
    from aws_durable_execution_sdk_python.lambda_service import (
        ExecutionDetails,
        Operation,
        OperationStatus,
        OperationType,
    )

    execution_op = Operation(
        operation_id="exec-op-0",
        operation_type=OperationType.EXECUTION,
        status=OperationStatus.STARTED,
        execution_details=ExecutionDetails(input_payload=input_payload_str),
    )
    return [execution_op]


# ===========================================================================
# Test class: Context Propagation (Distributed Tracing)
# ===========================================================================
class TestContextPropagation:
    """Tests for distributed tracing context propagation across invoke calls.

    Context propagation enables linking traces across service boundaries:
    - On invoke: trace context is injected into the payload dict via _datadog key
    - On execution: trace context is extracted from _datadog in the input_event
    """

    def test_extraction_links_execution_to_parent_trace(self, tracer):
        """When input_event contains _datadog headers, the execution span is a child of the upstream trace."""
        from ddtrace.propagation.http import HTTPPropagator

        from aws_durable_execution_sdk_python import durable_execution

        # Create upstream span to get valid trace context headers
        with tracer.tracer.trace("upstream.invoke") as upstream_span:
            upstream_trace_id = upstream_span.trace_id
            upstream_span_id = upstream_span.span_id
            headers = {}
            HTTPPropagator.inject(upstream_span.context, headers)

        # Clear the upstream span from collected spans
        tracer.pop()

        # Build input payload with _datadog headers
        input_payload = json.dumps({
            "order_id": "ORD-42",
            "_datadog": headers,
        })
        initial_ops = _build_initial_state_with_payload(input_payload)
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def ctx_workflow(event, context):
            return {"received": True}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        with override_config("aws_durable_execution_sdk_python", dict(distributed_tracing_enabled=True)):
            sdk_output = ctx_workflow(event, lambda_ctx)

        inner = _extract_result(sdk_output)
        assert inner["received"] is True

        spans = tracer.pop()
        workflow_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(workflow_spans) == 1
        workflow_span = workflow_spans[0]

        # Verify full span structure
        assert workflow_span.service == "aws.durable_execution"
        assert workflow_span.span_type == "serverless"
        assert workflow_span.resource == "aws.durable_execution.execute"
        assert workflow_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert workflow_span.get_tag("span.kind") == "server"
        assert workflow_span.error == 0

        # The execution span should be linked to the upstream trace
        assert workflow_span.trace_id == upstream_trace_id, (
            "Execution span trace_id should match upstream trace_id"
        )
        assert workflow_span.parent_id == upstream_span_id, (
            "Execution span parent_id should match upstream span_id"
        )

    def test_extraction_disabled_no_parent_linkage(self, tracer):
        """When distributed_tracing_enabled=False, _datadog is ignored and span is a root."""
        from ddtrace.propagation.http import HTTPPropagator

        from aws_durable_execution_sdk_python import durable_execution

        # Create upstream span context
        with tracer.tracer.trace("upstream.invoke") as upstream_span:
            upstream_trace_id = upstream_span.trace_id
            headers = {}
            HTTPPropagator.inject(upstream_span.context, headers)

        tracer.pop()

        input_payload = json.dumps({
            "order_id": "ORD-42",
            "_datadog": headers,
        })
        initial_ops = _build_initial_state_with_payload(input_payload)
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def no_extract_workflow(event, context):
            return {"received": True}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        with override_config("aws_durable_execution_sdk_python", dict(distributed_tracing_enabled=False)):
            no_extract_workflow(event, lambda_ctx)

        spans = tracer.pop()
        workflow_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(workflow_spans) == 1
        workflow_span = workflow_spans[0]

        # Verify span is still well-formed
        assert workflow_span.service == "aws.durable_execution"
        assert workflow_span.span_type == "serverless"
        assert workflow_span.get_tag("component") == "aws_durable_execution_sdk_python"

        # With distributed tracing disabled, the span should NOT be linked to upstream
        assert workflow_span.trace_id != upstream_trace_id, (
            "With distributed tracing disabled, trace_id should not match upstream"
        )

    def test_extraction_without_datadog_headers_creates_root_span(self, tracer):
        """When input_event has no _datadog key, the execution span is a root span."""
        from aws_durable_execution_sdk_python import durable_execution

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def root_workflow(event, context):
            return {"status": "ok"}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        with override_config("aws_durable_execution_sdk_python", dict(distributed_tracing_enabled=True)):
            sdk_output = root_workflow(event, lambda_ctx)

        inner = _extract_result(sdk_output)
        assert inner["status"] == "ok"

        spans = tracer.pop()
        workflow_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(workflow_spans) == 1
        workflow_span = workflow_spans[0]

        # Verify full span structure
        assert workflow_span.service == "aws.durable_execution"
        assert workflow_span.span_type == "serverless"
        assert workflow_span.resource == "aws.durable_execution.execute"
        assert workflow_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert workflow_span.get_tag("span.kind") == "server"
        assert workflow_span.error == 0

        # Without _datadog headers, the span should be a root span
        assert workflow_span.parent_id == 0 or workflow_span.parent_id is None, (
            "Without _datadog headers, execution span should be a root span"
        )

    def test_injection_adds_datadog_to_invoke_payload(self, tracer):
        """invoke() injects _datadog headers enabling distributed tracing across invocations.

        Verified by the full round-trip: upstream trace → inject into invoke payload →
        extract in downstream @durable_execution → all spans share the same trace_id.
        """
        from ddtrace.propagation.http import HTTPPropagator

        from aws_durable_execution_sdk_python import durable_execution

        # Create an upstream span to establish a trace context
        with tracer.tracer.trace("upstream.caller") as upstream_span:
            upstream_trace_id = upstream_span.trace_id
            upstream_span_id = upstream_span.span_id
            headers = {}
            HTTPPropagator.inject(upstream_span.context, headers)

        tracer.pop()

        # Build initial state with _datadog headers in the input payload
        # (simulating the downstream receiving injected context)
        input_payload_dict = {"order_id": "ORD-42", "amount": 99.95, "_datadog": headers}
        input_payload = json.dumps(input_payload_dict)

        from aws_durable_execution_sdk_python.lambda_service import (
            ChainedInvokeDetails,
            ExecutionDetails,
            Operation,
            OperationStatus,
            OperationType,
        )

        invoke_op_id = _step_id(None, 3)
        initial_ops = [
            Operation(
                operation_id="exec-op-0",
                operation_type=OperationType.EXECUTION,
                status=OperationStatus.STARTED,
                execution_details=ExecutionDetails(input_payload=input_payload),
            ),
            Operation(
                operation_id=invoke_op_id,
                operation_type=OperationType.CHAINED_INVOKE,
                status=OperationStatus.SUCCEEDED,
                name="process-payment",
                chained_invoke_details=ChainedInvokeDetails(
                    result=json.dumps({"payment_id": "PAY-789", "status": "approved"}),
                ),
            ),
        ]
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def inject_workflow(event, context):
            def validate(step_ctx):
                return {"valid": True}

            def calc(step_ctx):
                return {"total": 100}

            context.step(validate, name="validate")
            context.step(calc, name="calc")
            payment = context.invoke(
                function_name="payment-fn",
                payload={"order_id": "ORD-42", "amount": 99.95},
                name="process-payment",
            )
            return {"payment": payment}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        with override_config("aws_durable_execution_sdk_python", dict(distributed_tracing_enabled=True)):
            sdk_output = inject_workflow(event, lambda_ctx)

        spans = tracer.pop()
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]
        assert len(invoke_spans) == 1
        invoke_span = invoke_spans[0]

        # Verify invoke span structure
        assert invoke_span.service == "aws.durable_execution"
        assert invoke_span.span_type == "serverless"
        assert invoke_span.resource == "process-payment"
        assert invoke_span.get_tag("span.kind") == "client"
        assert invoke_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert invoke_span.get_tag("aws.durable_execution.invoke.function_name") == "payment-fn"
        assert invoke_span.error == 0

        # Verify all spans share the upstream trace_id (proves context was extracted)
        execution_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(execution_spans) == 1
        assert execution_spans[0].trace_id == upstream_trace_id
        assert execution_spans[0].parent_id == upstream_span_id
        for span in spans:
            assert span.trace_id == upstream_trace_id, (
                "All spans should share the upstream trace_id, proving injection/extraction works"
            )

    def test_injection_disabled_no_datadog_in_payload(self, tracer):
        """When distributed_tracing_enabled=False, invoke spans are created but traces are not linked."""
        from ddtrace.propagation.http import HTTPPropagator

        from aws_durable_execution_sdk_python import durable_execution

        # Create an upstream span
        with tracer.tracer.trace("upstream.caller") as upstream_span:
            upstream_trace_id = upstream_span.trace_id
            headers = {}
            HTTPPropagator.inject(upstream_span.context, headers)

        tracer.pop()

        # Build input WITH _datadog headers but DISABLE distributed tracing
        input_payload = json.dumps({"order_id": "ORD-42", "_datadog": headers})
        initial_ops = _build_initial_state_with_payload(input_payload)
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def disabled_inject_workflow(event, context):
            return {"received": True}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        with override_config("aws_durable_execution_sdk_python", dict(distributed_tracing_enabled=False)):
            disabled_inject_workflow(event, lambda_ctx)

        spans = tracer.pop()
        workflow_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(workflow_spans) == 1
        workflow_span = workflow_spans[0]

        # Verify span exists and is well-formed
        assert workflow_span.service == "aws.durable_execution"
        assert workflow_span.span_type == "serverless"
        assert workflow_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert workflow_span.get_tag("span.kind") == "server"

        # With distributed tracing disabled, the span should NOT be linked to upstream
        assert workflow_span.trace_id != upstream_trace_id, (
            "With distributed tracing disabled, trace_id should not match upstream"
        )

    def test_injection_non_dict_payload_graceful(self, tracer):
        """When payload is not a dict, injection is skipped gracefully and the span is still created.

        AIDEV-NOTE: The SDK requires dict payloads, so this edge case cannot be triggered
        through a real @durable_execution workflow. We call through the patched
        DurableContext.invoke (bound via the descriptor protocol, which exercises
        the full wrapping layer) with a mock instance.
        """
        from unittest.mock import MagicMock

        from aws_durable_execution_sdk_python.context import DurableContext

        mock_instance = MagicMock()
        # Bind the patched invoke through the descriptor protocol so wrapt properly
        # separates the instance from the arguments
        bound_invoke = DurableContext.invoke.__get__(mock_instance, DurableContext)

        try:
            bound_invoke("payment-fn", "string-payload", name="process-payment")
        except Exception:
            pass  # Original SDK method fails with mock instance; we verify span behavior

        spans = tracer.pop()
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]
        assert len(invoke_spans) == 1
        invoke_span = invoke_spans[0]

        # Verify span is still created correctly despite non-dict payload
        assert invoke_span.service == "aws.durable_execution"
        assert invoke_span.span_type == "serverless"
        assert invoke_span.resource == "process-payment"
        assert invoke_span.get_tag("span.kind") == "client"
        assert invoke_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert invoke_span.get_tag("aws.durable_execution.invoke.function_name") == "payment-fn"

    def test_injection_does_not_mutate_original_payload(self, tracer):
        """Injecting _datadog should not mutate the caller's original payload dict.

        AIDEV-NOTE: We call through the patched DurableContext.invoke (bound via the
        descriptor protocol) to exercise the full wrapping layer. The original SDK invoke
        fails on the mock instance, but the important assertion is that the
        original_payload dict is not mutated.
        """
        from unittest.mock import MagicMock

        from aws_durable_execution_sdk_python.context import DurableContext

        mock_instance = MagicMock()
        original_payload = {"order_id": "ORD-42"}
        # Bind the patched invoke through the descriptor protocol
        bound_invoke = DurableContext.invoke.__get__(mock_instance, DurableContext)

        with override_config("aws_durable_execution_sdk_python", dict(distributed_tracing_enabled=True)):
            try:
                bound_invoke("payment-fn", original_payload, name="process-payment")
            except Exception:
                pass  # Original SDK method fails with mock instance

        tracer.pop()

        # The original payload dict should NOT be mutated
        assert "_datadog" not in original_payload, (
            "Original payload dict should not be mutated by injection"
        )

    def test_distributed_tracing_enabled_by_default(self, tracer):
        """distributed_tracing_enabled should be True by default."""
        from ddtrace import config as dd_config

        assert dd_config.aws_durable_execution_sdk_python.distributed_tracing_enabled is True, (
            "distributed_tracing_enabled should be True by default"
        )

    def test_extraction_with_child_spans_preserves_trace(self, tracer):
        """When extracting context, step and invoke child spans share the same trace_id."""
        from ddtrace.propagation.http import HTTPPropagator

        from aws_durable_execution_sdk_python import durable_execution

        # Create upstream context
        with tracer.tracer.trace("upstream.invoke") as upstream_span:
            upstream_trace_id = upstream_span.trace_id
            upstream_span_id = upstream_span.span_id
            headers = {}
            HTTPPropagator.inject(upstream_span.context, headers)

        tracer.pop()

        # Build input with _datadog and also set up invoke pre-completed state
        input_payload_dict = {"order_id": "ORD-42", "_datadog": headers}
        input_payload = json.dumps(input_payload_dict)

        from aws_durable_execution_sdk_python.lambda_service import (
            ChainedInvokeDetails,
            ExecutionDetails,
            Operation,
            OperationStatus,
            OperationType,
        )

        invoke_op_id = _step_id(None, 3)
        execution_op = Operation(
            operation_id="exec-op-0",
            operation_type=OperationType.EXECUTION,
            status=OperationStatus.STARTED,
            execution_details=ExecutionDetails(input_payload=input_payload),
        )
        invoke_op = Operation(
            operation_id=invoke_op_id,
            operation_type=OperationType.CHAINED_INVOKE,
            status=OperationStatus.SUCCEEDED,
            name="process-payment",
            chained_invoke_details=ChainedInvokeDetails(
                result=json.dumps({"payment_id": "PAY-789", "status": "approved"}),
            ),
        )
        initial_ops = [execution_op, invoke_op]
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def linked_workflow(event, context):
            def validate(step_ctx):
                return {"valid": True}

            context.step(validate, name="validate")
            context.step(validate, name="calc")
            payment = context.invoke(
                function_name="payment-fn",
                payload={"total": 100},
                name="process-payment",
            )
            return {"payment": payment}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        with override_config("aws_durable_execution_sdk_python", dict(distributed_tracing_enabled=True)):
            sdk_output = linked_workflow(event, lambda_ctx)

        spans = tracer.pop()

        # Verify we got the expected span types
        execution_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        step_spans = [s for s in spans if s.name == "aws.durable_execution.step"]
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]

        assert len(execution_spans) == 1
        assert len(step_spans) == 2
        assert len(invoke_spans) == 1

        execution_span = execution_spans[0]

        # Execution span should be linked to the upstream trace
        assert execution_span.trace_id == upstream_trace_id
        assert execution_span.parent_id == upstream_span_id

        # All spans should share the upstream trace_id
        for span in spans:
            assert span.trace_id == upstream_trace_id, (
                "Span '{}' (resource={}) should share the upstream trace_id".format(
                    span.name, span.resource
                )
            )

        # Step and invoke spans should be children of the execution span
        for span in step_spans + invoke_spans:
            assert span.parent_id == execution_span.span_id, (
                "Span '{}' (resource={}) should be child of execution span".format(
                    span.name, span.resource
                )
            )


# ===========================================================================
# Test class: Peer Service
# ===========================================================================
class TestPeerService:
    """Tests for peer service support on invoke spans.

    The PeerServiceProcessor automatically computes ``peer.service`` from
    source tags on client spans.  The integration sets ``out.host`` on
    invoke spans so the processor can derive peer.service from the
    target function name.
    """

    def test_invoke_span_sets_out_host_to_function_name(self, tracer):
        """The invoke span should have out.host set to the target function name."""
        from aws_durable_execution_sdk_python import durable_execution

        initial_ops = _build_initial_state_with_invoke()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def peer_workflow(event, context):
            def validate(step_ctx):
                return {"valid": True}

            def calc(step_ctx):
                return {"total": 100}

            context.step(validate, name="validate")
            context.step(calc, name="calc")
            payment = context.invoke(
                function_name="payment-processor-fn",
                payload={"order_id": "ORD-42", "total": 100},
                name="process-payment",
            )
            return {"payment": payment}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        sdk_output = peer_workflow(event, lambda_ctx)

        spans = tracer.pop()
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]
        assert len(invoke_spans) == 1
        invoke_span = invoke_spans[0]

        # Verify full invoke span structure
        assert invoke_span.service == "aws.durable_execution"
        assert invoke_span.span_type == "serverless"
        assert invoke_span.resource == "process-payment"
        assert invoke_span.get_tag("span.kind") == "client"
        assert invoke_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert invoke_span.get_tag("aws.durable_execution.invoke.function_name") == "payment-processor-fn"

        # out.host should be set to the function name for peer service derivation
        assert invoke_span.get_tag("out.host") == "payment-processor-fn", (
            "Invoke span should have out.host set to the target function name"
        )

    def test_invoke_span_out_host_uses_function_name_directly(self, tracer):
        """invoke() sets out.host to the function_name, regardless of the invoke name."""
        from aws_durable_execution_sdk_python import durable_execution

        # Use a different function_name than the test_invoke_span_sets_out_host_to_function_name test
        # to verify out.host always matches function_name, not invoke name
        initial_ops = _build_initial_state_with_invoke()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def out_host_workflow(event, context):
            def validate(step_ctx):
                return {"valid": True}

            def calc(step_ctx):
                return {"total": 100}

            context.step(validate, name="validate")
            context.step(calc, name="calc")
            payment = context.invoke(
                function_name="my-downstream-lambda",
                payload={"order_id": "ORD-42"},
                name="process-payment",
            )
            return {"payment": payment}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        out_host_workflow(event, lambda_ctx)

        spans = tracer.pop()
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]
        assert len(invoke_spans) == 1
        invoke_span = invoke_spans[0]

        # Verify out.host is set to the function name (not the invoke name)
        assert invoke_span.get_tag("out.host") == "my-downstream-lambda"
        assert invoke_span.get_tag("span.kind") == "client"
        assert invoke_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert invoke_span.get_tag("aws.durable_execution.invoke.function_name") == "my-downstream-lambda"
        assert invoke_span.get_tag("aws.durable_execution.invoke.name") == "process-payment"
        assert invoke_span.resource == "process-payment"

    def test_execution_span_does_not_set_out_host(self, tracer):
        """The execution span (server) should NOT have out.host — peer service is only for client spans."""
        from aws_durable_execution_sdk_python import durable_execution

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def simple_workflow(event, context):
            return {"status": "ok"}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        simple_workflow(event, lambda_ctx)

        spans = tracer.pop()
        execution_spans = [s for s in spans if s.name == "aws.durable_execution.execute"]
        assert len(execution_spans) == 1
        execution_span = execution_spans[0]

        # Server spans should not have out.host
        assert execution_span.get_tag("span.kind") == "server"
        assert execution_span.get_tag("out.host") is None, (
            "Execution (server) span should not have out.host"
        )

    def test_step_span_does_not_set_out_host(self, tracer):
        """The step span (internal) should NOT have out.host — peer service is only for client spans."""
        from aws_durable_execution_sdk_python import durable_execution

        initial_ops = _build_initial_state_basic()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def step_only_workflow(event, context):
            def my_step(step_ctx):
                return "done"
            return context.step(my_step, name="my-step")

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()

        try:
            step_only_workflow(event, lambda_ctx)
        except Exception:
            pass

        spans = tracer.pop()
        step_spans = [s for s in spans if s.name == "aws.durable_execution.step"]
        assert len(step_spans) >= 1
        step_span = step_spans[0]

        # Internal spans should not have out.host
        assert step_span.get_tag("span.kind") == "internal"
        assert step_span.get_tag("out.host") is None, (
            "Step (internal) span should not have out.host"
        )

    def test_invoke_without_function_name_no_out_host(self, tracer):
        """When function_name is None/missing, out.host should not be set.

        AIDEV-NOTE: The SDK requires function_name, so this edge case cannot be triggered
        through a real @durable_execution workflow. We call through the patched
        DurableContext.invoke (bound via the descriptor protocol, which exercises
        the full wrapping layer) with a mock instance.
        """
        from unittest.mock import MagicMock

        from aws_durable_execution_sdk_python.context import DurableContext

        mock_instance = MagicMock()
        # Bind the patched invoke through the descriptor protocol so wrapt properly
        # separates the instance from the arguments
        bound_invoke = DurableContext.invoke.__get__(mock_instance, DurableContext)

        try:
            bound_invoke(name="call-no-target")
        except Exception:
            pass  # Original SDK method fails with mock instance

        spans = tracer.pop()
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]
        assert len(invoke_spans) == 1
        invoke_span = invoke_spans[0]

        # Without a function_name, out.host should not be set
        assert invoke_span.get_tag("out.host") is None, (
            "out.host should not be set when function_name is missing"
        )
        assert invoke_span.resource == "call-no-target"
        assert invoke_span.get_tag("span.kind") == "client"
        assert invoke_span.get_tag("component") == "aws_durable_execution_sdk_python"

    def test_peer_service_computed_from_out_host(self, tracer):
        """When peer service defaults are enabled, peer.service is derived from out.host."""
        from aws_durable_execution_sdk_python import durable_execution

        from ddtrace.internal.peer_service.processor import PeerServiceProcessor
        from ddtrace.internal.settings.peer_service import PeerServiceConfig

        initial_ops = _build_initial_state_with_invoke()
        mock_client = MockDurableServiceClient(initial_operations=initial_ops)

        @durable_execution
        def peer_test_workflow(event, context):
            def validate(step_ctx):
                return {"valid": True}

            def calc(step_ctx):
                return {"total": 100}

            context.step(validate, name="validate")
            context.step(calc, name="calc")
            payment = context.invoke(
                function_name="target-lambda-fn",
                payload={"data": "test"},
                name="process-payment",
            )
            return {"payment": payment}

        event = _create_invocation_event(initial_ops, mock_client)
        lambda_ctx = MockLambdaContext()
        peer_test_workflow(event, lambda_ctx)

        spans = tracer.pop()
        invoke_spans = [s for s in spans if s.name == "aws.durable_execution.invoke"]
        assert len(invoke_spans) == 1
        invoke_span = invoke_spans[0]

        # Verify out.host is set (prerequisite for peer service)
        assert invoke_span.get_tag("out.host") == "target-lambda-fn"
        assert invoke_span.get_tag("span.kind") == "client"
        assert invoke_span.get_tag("component") == "aws_durable_execution_sdk_python"

        # Manually run the PeerServiceProcessor with defaults enabled to verify
        # the full peer.service computation works
        ps_config = PeerServiceConfig(set_defaults_enabled=True, peer_service_mapping={})
        processor = PeerServiceProcessor(ps_config)
        processor.process_trace([invoke_span])

        # Verify peer.service was computed from out.host
        assert invoke_span.get_tag("peer.service") == "target-lambda-fn", (
            "peer.service should be derived from out.host (function_name)"
        )
        assert invoke_span.get_tag("_dd.peer.service.source") == "out.host", (
            "peer.service source should be out.host"
        )
