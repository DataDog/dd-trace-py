"""Tests for Pydantic AI evaluator integration with LLMObs experiments."""

import asyncio

import pytest

from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import _ExperimentRunInfo
from ddtrace.llmobs._experiment import _is_pydantic_evaluator

from pydantic_evals.evaluators import Evaluator
from pydantic_evals.evaluators import EvaluatorContext

  


def _make_simple_pydantic_evaluator():
    """Build a minimal pydantic_evals Evaluator for tests (only when pydantic_evals is available)."""
    from dataclasses import dataclass

    @dataclass
    class SimplePydanticEvaluator(Evaluator):
        """Minimal pydantic_evals evaluator: True when output equals expected_output, else False."""

        evaluation_name: str = "simple_pydantic_eval"

        def evaluate(self, ctx: EvaluatorContext) -> bool:
            if ctx.expected_output is None:
                return False
            return ctx.output == ctx.expected_output

    return SimplePydanticEvaluator()


class TestPydanticEvaluatorDetection:
    """Test that _is_pydantic_evaluator correctly identifies pydantic_evals evaluators."""

    def test_simple_evaluator_is_pydantic_evaluator(self):
        evaluator = _make_simple_pydantic_evaluator()
        assert _is_pydantic_evaluator(evaluator) is True

    def test_function_is_not_pydantic_evaluator(self):
        def fn(a, b, c):
            return 1

        assert _is_pydantic_evaluator(fn) is False


class TestPydanticEvaluatorEvaluate:
    """Test that a pydantic evaluator runs successfully (evaluate returns bool)."""

    def test_evaluate_pass(self):
        from pydantic_evals.evaluators import EvaluatorContext

        evaluator = _make_simple_pydantic_evaluator()
        ctx = EvaluatorContext(
            name="",
            inputs={"value": "test"},
            expected_output="Paris",
            output="Paris",
            duration=0.0,
            metadata=None,
            _span_tree=None,
            attributes={},
            metrics={},
        )
        result = evaluator.evaluate(ctx)
        assert result is True

    def test_evaluate_fail(self):
        from pydantic_evals.evaluators import EvaluatorContext

        evaluator = _make_simple_pydantic_evaluator()
        ctx = EvaluatorContext(
            name="",
            inputs={"value": "test"},
            expected_output="Paris",
            output="London",
            duration=0.0,
            metadata=None,
            _span_tree=None,
            attributes={},
            metrics={},
        )
        result = evaluator.evaluate(ctx)
        assert result is False


class TestPydanticEvaluatorInExperiment:
    """Test that a pydantic evaluator runs successfully inside an experiment run."""

    def test_experiment_run_with_pydantic_evaluator(self, llmobs):
        """Run an experiment with a pydantic evaluator and assert it completes with correct results."""

        def dummytask(input_data, config):
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

        pydantic_evaluator = _make_simple_pydantic_evaluator()
        exp = llmobs.experiment(
            "test_experiment",
            dummytask,
            dataset,
            [pydantic_evaluator],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = asyncio.run(exp._experiment._run_task(1, run=run_info, raise_errors=False))
        eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))

        assert len(eval_results) == 1
        assert "simple_pydantic_eval" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["simple_pydantic_eval"]
        assert result["error"] is None
        assert result["value"] is True
        assert result["assessment"] == "pass"

    def test_experiment_run_with_pydantic_evaluator_fail(self, llmobs):
        """Pydantic evaluator returns False when output != expected_output."""

        def task(input_data, config):
            return "London"

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"value": "test"},
                    "expected_output": "Paris",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        pydantic_evaluator = _make_simple_pydantic_evaluator()
        exp = llmobs.experiment(
            "test_experiment",
            task,
            dataset,
            [pydantic_evaluator],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = asyncio.run(exp._experiment._run_task(1, run=run_info, raise_errors=False))
        eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))

        assert len(eval_results) == 1
        assert "simple_pydantic_eval" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["simple_pydantic_eval"]
        assert result["value"] is False
        assert result["assessment"] == "fail"
