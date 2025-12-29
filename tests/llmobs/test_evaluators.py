"""Tests for LLMObs evaluator classes."""

import pytest

from ddtrace.llmobs._evaluators.base import BaseEvaluator
from ddtrace.llmobs._evaluators.base import EvaluatorContext
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge


class SimpleEvaluator(BaseEvaluator):
    """A simple test evaluator that checks if output equals expected."""

    def evaluate(self, context: EvaluatorContext):
        passed = context.output_data == context.expected_output
        return {"passed": passed, "score": 1.0 if passed else 0.0}


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


class AsyncEvaluator(BaseEvaluator):
    """An evaluator with async support."""

    async def evaluate_async(self, context: EvaluatorContext):
        # Simulate async operation
        passed = context.output_data == context.expected_output
        return {"passed": passed, "score": 1.0 if passed else 0.0, "async": True}


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
        assert ctx.config == {}

    def test_context_with_optional_fields(self):
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="expected",
            metadata={"key": "value"},
            span_id="span_123",
            trace_id="trace_456",
            config={"temperature": 0.7},
        )
        assert ctx.metadata == {"key": "value"}
        assert ctx.span_id == "span_123"
        assert ctx.trace_id == "trace_456"
        assert ctx.config == {"temperature": 0.7}

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

    def test_evaluator_legacy_call_interface(self):
        """Test that evaluators work with the legacy function signature."""
        evaluator = SimpleEvaluator()
        result = evaluator(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        assert result == {"passed": True, "score": 1.0}

    def test_evaluator_name_validation_invalid_characters(self):
        """Test that names with invalid characters are rejected."""
        with pytest.raises(ValueError, match="Evaluator name .* is invalid"):
            SimpleEvaluator(name="my-evaluator")

    def test_evaluator_name_validation_valid_characters(self):
        """Test that valid names are accepted."""
        evaluator = SimpleEvaluator(name="my_evaluator_123")
        assert evaluator.name == "my_evaluator_123"

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


class TestAsyncEvaluator:
    @pytest.mark.asyncio
    async def test_async_evaluate(self):
        evaluator = AsyncEvaluator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        result = await evaluator.evaluate_async(ctx)
        assert result == {"passed": True, "score": 1.0, "async": True}

    def test_sync_evaluator_async_fallback(self):
        """Test that sync evaluators fall back to sync in async context."""
        import asyncio

        evaluator = SimpleEvaluator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )

        # Should fall back to sync evaluate
        result = asyncio.run(evaluator.evaluate_async(ctx))
        assert result == {"passed": True, "score": 1.0}


class TestLLMJudge:
    def test_llm_judge_creation(self):
        judge = LLMJudge(
            system_prompt="You are a grader.",
            model_config={"model": "gpt-4o", "temperature": 0.0},
        )
        assert judge.system_prompt == "You are a grader."
        assert judge.model_config["model"] == "gpt-4o"
        assert judge.model_config["temperature"] == 0.0
        assert judge.name == "LLMJudge"

    def test_llm_judge_custom_name(self):
        judge = LLMJudge(
            system_prompt="You are a grader.",
            name="quality_judge",
        )
        assert judge.name == "quality_judge"

    def test_llm_judge_default_temperature(self):
        judge = LLMJudge(
            system_prompt="You are a grader.",
            model_config={"model": "gpt-4o"},
        )
        assert judge.model_config["temperature"] == 0.0

    def test_llm_judge_evaluate(self):
        """Test basic evaluation (with placeholder LLM)."""
        judge = LLMJudge(system_prompt="You are a grader.")
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        result = judge.evaluate(ctx)
        assert "score" in result
        assert isinstance(result["score"], float)

    @pytest.mark.asyncio
    async def test_llm_judge_evaluate_async(self):
        """Test async evaluation (with placeholder LLM)."""
        judge = LLMJudge(system_prompt="You are a grader.")
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        result = await judge.evaluate_async(ctx)
        assert "score" in result
        assert isinstance(result["score"], float)


class TestEvaluatorIntegration:
    """Test evaluators with the experiment system."""

    def test_class_evaluator_with_experiment(self, llmobs):
        """Test that class-based evaluators work with experiments."""
        from ddtrace.llmobs._experiment import Dataset
        from ddtrace.llmobs._experiment import _ExperimentRunInfo

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
        from ddtrace.llmobs._experiment import Dataset
        from ddtrace.llmobs._experiment import _ExperimentRunInfo

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
