"""Tests for DeepEval evaluator integration with LLMObs experiments."""

import mock
import pytest

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

from ddtrace.llmobs._experiment import _is_deep_eval_evaluator
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import _ExperimentRunInfo

class SimpleDeepEvalMetric(BaseMetric):
    """Minimal DeepEval metric for tests: scores 1.0 when actual equals expected, else 0.0."""

    def __init__(self, name="SimpleDeepEvalMetric", **kwargs):
        super().__init__(**kwargs)
        self._name = name

    @property
    def name(self):
        return self._name

    def measure(self, test_case: LLMTestCase) -> float:
        passed = test_case.actual_output == test_case.expected_output
        self.score = 1.0 if passed else 0.0
        self.reason = "Match" if passed else "Mismatch"
        self.success = passed
        return self.score


class TestDeepEvalEvaluatorDetection:
    """Test that _is_deep_eval_evaluator correctly identifies DeepEval metrics."""

    def test_simple_metric_is_deep_eval_evaluator(self):
        evaluator = SimpleDeepEvalMetric()
        assert _is_deep_eval_evaluator(evaluator) is True

    def test_function_is_not_deep_eval_evaluator(self):
        def fn(a, b, c):
            return 1

        assert _is_deep_eval_evaluator(fn) is False


class TestDeepEvalEvaluatorMeasure:
    """Test that a DeepEval evaluator runs successfully (measure + score/reason/success)."""

    def test_measure_sets_score_pass(self):
        metric = SimpleDeepEvalMetric()
        tc = LLMTestCase(input="q", actual_output="Paris", expected_output="Paris")
        metric.measure(tc)
        assert metric.score == 1.0
        assert metric.reason == "Match"
        assert metric.success is True

    def test_measure_sets_score_fail(self):
        metric = SimpleDeepEvalMetric()
        tc = LLMTestCase(input="q", actual_output="London", expected_output="Paris")
        metric.measure(tc)
        assert metric.score == 0.0
        assert metric.reason == "Mismatch"
        assert metric.success is False


class TestDeepEvalEvaluatorInExperiment:
    """Test that a DeepEval evaluator runs successfully inside an experiment run."""

    def test_experiment_run_with_deep_eval_evaluator(self, llmobs):
        """Run an experiment with a DeepEval evaluator and assert it completes with correct results."""
        
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

        deep_eval_metric = SimpleDeepEvalMetric(name="simple_deep_eval")
        exp = llmobs.experiment(
            "test_experiment",
            dummytask,
            dataset,
            [deep_eval_metric],
        )
        
        run_info = _ExperimentRunInfo(0)
        task_results = exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = exp._run_evaluators(task_results, raise_errors=False)

        assert len(eval_results) == 1
        assert "simple_deep_eval" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["simple_deep_eval"]
        assert result["error"] is None
        assert result["value"] == 1.0
        assert result["reasoning"] == "Match"
        assert result["assessment"] == "pass"

    def test_experiment_run_with_deep_eval_evaluator_fail(self, llmobs):
        """DeepEval evaluator scores 0 when actual_output != expected_output."""

        def task(input_data, config):
            return {"answer": "London"}
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

        deep_eval_metric = SimpleDeepEvalMetric(name="simple_deep_eval")
        exp = llmobs.experiment(
            "test_experiment",
            task,
            dataset,
            [deep_eval_metric],
        )
        
        run_info = _ExperimentRunInfo(0)
        task_results = exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = exp._run_evaluators(task_results, raise_errors=False)

        assert len(eval_results) == 1
        assert "simple_deep_eval" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["simple_deep_eval"]
        assert result["value"] == 0.0
        assert result["reasoning"] == "Mismatch"
        assert result["assessment"] == "fail"
