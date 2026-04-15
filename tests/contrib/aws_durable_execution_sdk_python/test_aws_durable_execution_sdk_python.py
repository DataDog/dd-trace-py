"""
APM span tests for the aws_durable_execution_sdk_python integration.

Tests verify that the integration produces correct spans with proper names,
types, tags, and parent-child relationships for:
  1. durable_execution — top-level workflow execution span
  2. DurableContext.step — individual step execution spans
  3. DurableContext.invoke — cross-function invocation spans

These tests use a mock DurableServiceClient to simulate the checkpoint/state
management that normally runs against the real AWS Lambda Durable Execution API.
"""
import hashlib
import json
from dataclasses import dataclass
from threading import Lock
from typing import Any

import pytest

from ddtrace._trace.pin import Pin
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
        result = my_workflow(event, lambda_ctx)

        assert result is not None
        assert result["validation"]["valid"] is True

        spans = tracer.pop()
        # AIDEV-NOTE: Expect at least 2 spans: 1 workflow execution + 1 step
        assert len(spans) >= 2

        # Find the workflow execution span (root span)
        workflow_spans = [s for s in spans if s.name == "aws_durable_execution.execution"]
        assert len(workflow_spans) == 1, "Expected exactly one workflow execution span"
        workflow_span = workflow_spans[0]

        # Verify span structure
        assert workflow_span.service == "aws_durable_execution_sdk_python"
        assert workflow_span.span_type == "serverless"
        assert workflow_span.resource == "aws_durable_execution.execution"
        assert workflow_span.error == 0

        # Verify span tags
        assert workflow_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert workflow_span.get_tag("span.kind") == "server"
        assert workflow_span.get_tag("aws.durable_execution.arn") == DURABLE_EXECUTION_ARN

        # The workflow span should be the parent of step spans
        step_spans = [s for s in spans if s.name == "aws_durable_execution.step"]
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

        with pytest.raises(ValueError, match="Workflow failed"):
            failing_workflow(event, lambda_ctx)

        spans = tracer.pop()
        workflow_spans = [s for s in spans if s.name == "aws_durable_execution.execution"]
        assert len(workflow_spans) == 1
        workflow_span = workflow_spans[0]

        # Verify error is captured on the span
        assert workflow_span.error == 1
        assert workflow_span.get_tag(ERROR_TYPE) == "ValueError"
        assert "Workflow failed: invalid input data" in workflow_span.get_tag(ERROR_MSG)
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
        workflow_spans = [s for s in spans if s.name == "aws_durable_execution.execution"]
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
        result = step_workflow(event, lambda_ctx)

        assert result["validation"]["valid"] is True
        assert result["totals"]["total"] == 107.95

        spans = tracer.pop()
        step_spans = [s for s in spans if s.name == "aws_durable_execution.step"]
        assert len(step_spans) == 2, "Expected exactly 2 step spans"

        # Verify first step span
        validate_spans = [s for s in step_spans if s.get_tag("aws.durable_execution.step.name") == "validate-order"]
        assert len(validate_spans) == 1, "Expected one 'validate-order' step span"
        validate_span = validate_spans[0]
        assert validate_span.service == "aws_durable_execution_sdk_python"
        assert validate_span.span_type == "worker"
        assert validate_span.resource == "validate-order"
        assert validate_span.error == 0
        assert validate_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert validate_span.get_tag("span.kind") == "internal"
        assert validate_span.get_tag("aws.durable_execution.step.name") == "validate-order"
        # Operation ID should be present
        assert validate_span.get_tag("aws.durable_execution.step.operation_id") is not None

        # Verify second step span
        totals_spans = [s for s in step_spans if s.get_tag("aws.durable_execution.step.name") == "calculate-totals"]
        assert len(totals_spans) == 1, "Expected one 'calculate-totals' step span"
        totals_span = totals_spans[0]
        assert totals_span.service == "aws_durable_execution_sdk_python"
        assert totals_span.span_type == "worker"
        assert totals_span.resource == "calculate-totals"
        assert totals_span.error == 0
        assert totals_span.get_tag("component") == "aws_durable_execution_sdk_python"
        assert totals_span.get_tag("span.kind") == "internal"

    def test_step_error_captures_exception(self, tracer):
        """When a step function raises an exception, the step span captures error details."""
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

        with pytest.raises(Exception):
            error_step_workflow(event, lambda_ctx)

        spans = tracer.pop()
        step_spans = [s for s in spans if s.name == "aws_durable_execution.step"]

        # At least the failing step should have a span
        failing_spans = [
            s for s in step_spans
            if s.get_tag("aws.durable_execution.step.name") == "failing-step"
        ]
        assert len(failing_spans) >= 1, "Expected at least one span for the failing step"
        failing_span = failing_spans[0]

        assert failing_span.error == 1
        assert failing_span.get_tag(ERROR_TYPE) is not None
        assert failing_span.get_tag(ERROR_MSG) is not None
        assert "failed" in failing_span.get_tag(ERROR_MSG).lower()

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

        try:
            unnamed_step_workflow(event, lambda_ctx)
        except Exception:
            pass  # May fail, we just need to check span naming

        spans = tracer.pop()
        step_spans = [s for s in spans if s.name == "aws_durable_execution.step"]
        assert len(step_spans) >= 1, "Expected at least one step span"

        # When no explicit name is given, the step name should be derived from the function
        step_span = step_spans[0]
        step_name_tag = step_span.get_tag("aws.durable_execution.step.name")
        # The SDK derives the name from the function; we verify it's populated
        assert step_name_tag is not None, "Step name tag should be set even without explicit name"


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
        invoke_spans = [s for s in spans if s.name == "aws_durable_execution.invoke"]
        assert len(invoke_spans) == 1, "Expected exactly one invoke span"
        invoke_span = invoke_spans[0]

        # Verify span structure
        assert invoke_span.service == "aws_durable_execution_sdk_python"
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
        workflow_spans = [s for s in spans if s.name == "aws_durable_execution.execution"]
        invoke_spans = [s for s in spans if s.name == "aws_durable_execution.invoke"]

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
          aws_durable_execution.execution (root)
            |- aws_durable_execution.step (validate-order)
            |- aws_durable_execution.step (calculate-totals)
            |- aws_durable_execution.invoke (process-payment)
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
        result = full_workflow(event, lambda_ctx)

        # Verify the workflow completed successfully
        assert result["validation"]["valid"] is True
        assert result["totals"]["total"] == 107.95
        assert result["payment"]["status"] == "approved"

        spans = tracer.pop()

        # Verify total span count: 1 execution + 2 steps + 1 invoke = 4
        assert len(spans) == 4, "Expected 4 spans: 1 execution + 2 steps + 1 invoke, got {}".format(len(spans))

        # Categorize spans
        execution_spans = [s for s in spans if s.name == "aws_durable_execution.execution"]
        step_spans = [s for s in spans if s.name == "aws_durable_execution.step"]
        invoke_spans = [s for s in spans if s.name == "aws_durable_execution.invoke"]

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
        result = empty_workflow(event, lambda_ctx)

        assert result["status"] == "completed"

        spans = tracer.pop()
        assert len(spans) == 1, "A no-op workflow should produce exactly 1 execution span"
        assert spans[0].name == "aws_durable_execution.execution"
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
        workflow_spans = [s for s in spans if s.name == "aws_durable_execution.execution"]
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
        result = idempotent_workflow(event, lambda_ctx)

        assert result["result"] == "ok"

        spans = tracer.pop()
        # Should get exactly 1 execution span, not duplicated by double patching
        execution_spans = [s for s in spans if s.name == "aws_durable_execution.execution"]
        assert len(execution_spans) == 1, "Double patching should not produce duplicate spans"
