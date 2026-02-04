"""Tests for LLMObs evaluator classes."""

import pytest

from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import BaseSummaryEvaluator
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import SummaryEvaluatorContext
from ddtrace.llmobs._experiment import _ExperimentRunInfo


class SimpleEvaluator(BaseEvaluator):
    """A simple test evaluator that checks if output equals expected."""

    def evaluate(self, context: EvaluatorContext):
        passed = context.output_data == context.expected_output
        return {"passed": passed, "score": 1.0 if passed else 0.0}


class PrimitiveEvaluator(BaseEvaluator):
    """An evaluator that returns primitive types (like function-based evaluators)."""

    def evaluate(self, context: EvaluatorContext):
        # Return int like function-based evaluators can do
        return int(context.output_data == context.expected_output)


class StatefulEvaluator(BaseEvaluator):
    """An evaluator with internal state."""

    def __init__(self, threshold=0.5):
        super().__init__()
        self.threshold = threshold
        self.call_count = 0

    def evaluate(self, context: EvaluatorContext):
        self.call_count += 1
        score = float(context.output_data == context.expected_output)
        return {
            "passed": score >= self.threshold,
            "score": score,
            "call_count": self.call_count,
        }


class TestEvaluatorContext:
    def test_context_creation(self):
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="expected",
        )
        assert ctx.input_data == {"query": "test"}
        assert ctx.output_data == "response"
        assert ctx.expected_output == "expected"
        assert ctx.metadata == {}
        assert ctx.span_id is None
        assert ctx.trace_id is None

    def test_context_with_optional_fields(self):
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="expected",
            metadata={"key": "value", "experiment_config": {"temperature": 0.7}},
            span_id="span_123",
            trace_id="trace_456",
        )
        assert ctx.metadata == {"key": "value", "experiment_config": {"temperature": 0.7}}
        assert ctx.span_id == "span_123"
        assert ctx.trace_id == "trace_456"
        assert ctx.metadata.get("experiment_config") == {"temperature": 0.7}

    def test_context_is_frozen(self):
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
        )
        with pytest.raises(Exception):  # FrozenInstanceError in Python 3.10+
            ctx.output_data = "new_value"


class TestBaseEvaluator:
    def test_evaluator_name_default(self):
        evaluator = SimpleEvaluator()
        assert evaluator.name == "SimpleEvaluator"

    def test_evaluator_name_custom(self):
        evaluator = SimpleEvaluator(name="custom_name")
        assert evaluator.name == "custom_name"

    def test_evaluator_evaluate(self):
        evaluator = SimpleEvaluator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        result = evaluator.evaluate(ctx)
        assert result == {"passed": True, "score": 1.0}

    def test_evaluator_name_validation_invalid_characters(self):
        """Test that names with invalid characters are rejected."""
        with pytest.raises(ValueError, match="Evaluator name .* is invalid"):
            SimpleEvaluator(name="my-evaluator")

    def test_evaluator_name_validation_valid_characters(self):
        """Test that valid names are accepted."""
        evaluator = SimpleEvaluator(name="my_evaluator_123")
        assert evaluator.name == "my_evaluator_123"

    def test_evaluator_primitive_return_type(self):
        """Test that evaluators can return primitive types like function-based evaluators."""
        evaluator = PrimitiveEvaluator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1  # Returns int directly

        ctx2 = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="different",
        )
        result2 = evaluator.evaluate(ctx2)
        assert result2 == 0

    def test_stateful_evaluator(self):
        evaluator = StatefulEvaluator(threshold=0.8)
        assert evaluator.threshold == 0.8
        assert evaluator.call_count == 0

        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        result = evaluator.evaluate(ctx)
        assert result["call_count"] == 1
        assert result["passed"] is True

        # Call again
        result = evaluator.evaluate(ctx)
        assert result["call_count"] == 2


class TestEvaluatorIntegration:
    """Test evaluators with the experiment system."""

    def test_class_evaluator_with_experiment(self, llmobs):
        """Test that class-based evaluators work with experiments."""

        def dummy_task(input_data, config):
            return input_data.get("value", "")

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"value": "test"},
                    "expected_output": "test",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        evaluator = SimpleEvaluator()
        exp = llmobs.experiment("test_experiment", dummy_task, dataset, [evaluator])

        run_info = _ExperimentRunInfo(0)
        task_results = exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = exp._run_evaluators(task_results, raise_errors=False)

        assert len(eval_results) == 1
        assert "SimpleEvaluator" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["SimpleEvaluator"]
        assert result["error"] is None
        assert result["value"]["passed"] is True

    def test_mixed_evaluators_with_experiment(self, llmobs):
        """Test mixing function and class-based evaluators."""

        def dummy_task(input_data, config):
            return input_data.get("value", "")

        def function_evaluator(input_data, output_data, expected_output):
            return {"type": "function", "passed": output_data == expected_output}

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"value": "test"},
                    "expected_output": "test",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        class_evaluator = SimpleEvaluator()
        exp = llmobs.experiment(
            "test_experiment",
            dummy_task,
            dataset,
            [function_evaluator, class_evaluator],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = exp._run_evaluators(task_results, raise_errors=False)

        assert len(eval_results) == 1
        evaluations = eval_results[0]["evaluations"]
        assert "function_evaluator" in evaluations
        assert "SimpleEvaluator" in evaluations
        assert evaluations["function_evaluator"]["value"]["type"] == "function"
        assert evaluations["SimpleEvaluator"]["value"]["passed"] is True


class SimpleSummaryEvaluator(BaseSummaryEvaluator):
    """A simple test summary evaluator."""

    def evaluate(self, context: SummaryEvaluatorContext):
        return len(context.inputs) + len(context.outputs)


class TestSummaryEvaluatorContext:
    def test_context_creation(self):
        ctx = SummaryEvaluatorContext(
            inputs=[{"query": "test"}],
            outputs=["response"],
            expected_outputs=["expected"],
            evaluation_results={"eval1": [1.0]},
        )
        assert ctx.inputs == [{"query": "test"}]
        assert ctx.outputs == ["response"]
        assert ctx.evaluation_results == {"eval1": [1.0]}


class TestSummaryEvaluatorIntegration:
    def test_class_summary_evaluator(self, llmobs):
        def dummy_task(input_data, config):
            return input_data.get("value", "")

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {"record_id": "rec_1", "input_data": {"value": "test"}, "expected_output": "test", "metadata": {}}
            ],
            description="",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        exp = llmobs.experiment(
            "test_experiment", dummy_task, dataset, [SimpleEvaluator()], summary_evaluators=[SimpleSummaryEvaluator()]
        )

        run_info = _ExperimentRunInfo(0)
        task_results = exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = exp._run_evaluators(task_results, raise_errors=False)
        summary_results = exp._run_summary_evaluators(task_results, eval_results, raise_errors=False)

        assert "SimpleSummaryEvaluator" in summary_results[0]["evaluations"]
        assert summary_results[0]["evaluations"]["SimpleSummaryEvaluator"]["value"] == 2
