"""Tests for ddtrace.llmobs._prompt_optimization and ddtrace.llmobs._optimizers.gepa_strategy."""
# python -m pytest tests/llmobs/test_prompt_optimization.py -v --no-header --tb=short --override-ini="addopts=" 2>&1

import random
import sys
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.llmobs._evaluators import BaseEvaluator
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._experiment import ExperimentRowResult
from ddtrace.llmobs._experiment import Project
from ddtrace.llmobs._prompt_optimization import TIPS
from ddtrace.llmobs._prompt_optimization import IterationData
from ddtrace.llmobs._prompt_optimization import OptimizationIteration
from ddtrace.llmobs._prompt_optimization import OptimizationResult
from ddtrace.llmobs._prompt_optimization import PromptOptimization
from ddtrace.llmobs._prompt_optimization import TestPhaseResult
from ddtrace.llmobs._prompt_optimization import load_optimization_system_prompt
from ddtrace.llmobs._prompt_optimization import validate_dataset
from ddtrace.llmobs._prompt_optimization import validate_dataset_split
from ddtrace.llmobs._prompt_optimization import validate_evaluators
from ddtrace.llmobs._prompt_optimization import validate_optimization_task
from ddtrace.llmobs._prompt_optimization import validate_task
from ddtrace.llmobs._prompt_optimization import validate_test_dataset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def dummy_task(input_data, config):
    return input_data


def dummy_optimization_task(system_prompt, user_prompt, config):
    return "improved prompt"


def dummy_evaluator(input_data, output_data, expected_output):
    return int(output_data == expected_output)


def dummy_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
    correct = sum(1 for r in evaluators_results.get("dummy_evaluator", []) if r == 1)
    return {"accuracy": correct / len(inputs) if inputs else 0.0}


def dummy_compute_score(summary_evals):
    return summary_evals.get("dummy_summary_evaluator", {}).get("value", {}).get("accuracy", 0.0)


def dummy_labelization(result):
    val = result.get("evaluations", {}).get("dummy_evaluator", {}).get("value", 0)
    return "correct" if val == 1 else "incorrect"


def _make_dataset(name="test_ds", num_records=5):
    project: Project = {"name": "test_project", "_id": "proj-123"}
    records = []
    for i in range(num_records):
        rec: DatasetRecord = {
            "record_id": f"rec-{i}",
            "input_data": {"question": f"q{i}"},
            "expected_output": {"answer": f"a{i}"},
            "metadata": {"idx": i},
        }
        records.append(rec)
    return Dataset(
        name=name,
        project=project,
        dataset_id="ds-123",
        records=records,
        description="test dataset",
        latest_version=1,
        version=1,
        _dne_client=MagicMock(),
    )


def _make_experiment_result(score_value=0.5, num_rows=2):
    """Build a minimal ExperimentResult TypedDict with controlled summary and rows."""
    rows = []
    for i in range(num_rows):
        row: ExperimentRowResult = {
            "idx": i,
            "record_id": f"rec-{i}",
            "span_id": f"span-{i}",
            "trace_id": f"trace-{i}",
            "timestamp": 1000000 + i,
            "input": {"question": f"q{i}"},
            "output": {"answer": f"a{i}"},
            "expected_output": {"answer": f"a{i}"},
            "evaluations": {
                "dummy_evaluator": {"value": 1, "error": None},
            },
            "metadata": {},
            "error": {"message": None, "stack": None, "type": None},
        }
        rows.append(row)
    result: ExperimentResult = {
        "summary_evaluations": {
            "dummy_summary_evaluator": {
                "value": {"accuracy": score_value},
                "error": None,
            }
        },
        "rows": rows,
        "runs": [],
    }
    return result


def _make_prompt_optimization(**overrides):
    """Build a PromptOptimization instance with sensible defaults."""
    mock_llmobs = MagicMock()
    mock_llmobs.enabled = True
    defaults = {
        "name": "test_opt",
        "task": dummy_task,
        "optimization_task": dummy_optimization_task,
        "dataset": _make_dataset(),
        "evaluators": [dummy_evaluator],
        "project_name": "test_project",
        "config": {"prompt": "initial prompt", "model_name": "gpt-4"},
        "summary_evaluators": [dummy_summary_evaluator],
        "compute_score": dummy_compute_score,
        "labelization_function": dummy_labelization,
        "_llmobs_instance": mock_llmobs,
    }
    defaults.update(overrides)
    return PromptOptimization(**defaults)


# ===========================================================================
# 1. Validation Functions
# ===========================================================================


class TestValidationFunctions:
    def test_validate_task_valid(self):
        validate_task(dummy_task)  # should not raise

    def test_validate_task_not_callable(self):
        with pytest.raises(TypeError, match="task must be a callable"):
            validate_task("not_a_function")

    def test_validate_task_missing_input_data(self):
        def bad_task(config):
            pass

        with pytest.raises(TypeError, match="input_data"):
            validate_task(bad_task)

    def test_validate_task_missing_config(self):
        def bad_task(input_data):
            pass

        with pytest.raises(TypeError, match="config"):
            validate_task(bad_task)

    def test_validate_optimization_task_valid(self):
        validate_optimization_task(dummy_optimization_task)  # should not raise

    def test_validate_optimization_task_not_callable(self):
        with pytest.raises(TypeError, match="optimization_task must be a callable"):
            validate_optimization_task(42)

    def test_validate_optimization_task_missing_params(self):
        def bad_opt_task(system_prompt):
            pass

        with pytest.raises(TypeError, match="user_prompt"):
            validate_optimization_task(bad_opt_task)

    def test_validate_dataset_valid(self):
        ds = _make_dataset()
        validate_dataset(ds)  # should not raise

    def test_validate_dataset_invalid_type(self):
        with pytest.raises(TypeError, match="Dataset must be an LLMObs Dataset"):
            validate_dataset({"records": []})

    def test_validate_test_dataset_none(self):
        validate_test_dataset(None)  # should not raise

    def test_validate_test_dataset_string(self):
        validate_test_dataset("my_test_dataset")  # should not raise

    def test_validate_test_dataset_invalid(self):
        with pytest.raises(TypeError, match="test_dataset must be a dataset name"):
            validate_test_dataset(123)

    def test_validate_dataset_split_valid_3_tuple(self):
        validate_dataset_split((0.6, 0.2, 0.2), None)  # should not raise

    def test_validate_dataset_split_valid_2_tuple_with_test(self):
        validate_dataset_split((0.8, 0.2), "test_ds")  # should not raise

    def test_validate_dataset_split_bad_sum(self):
        with pytest.raises(ValueError, match="must sum to 1.0"):
            validate_dataset_split((0.5, 0.2, 0.1), None)

    def test_validate_dataset_split_out_of_range(self):
        with pytest.raises(ValueError, match="between 0 and 1"):
            validate_dataset_split((0.0, 0.5, 0.5), None)

    def test_validate_dataset_split_wrong_length(self):
        with pytest.raises(ValueError, match="must have 2 or 3 elements"):
            validate_dataset_split((0.25, 0.25, 0.25, 0.25), None)

    def test_validate_dataset_split_3_tuple_with_test_dataset_conflict(self):
        with pytest.raises(ValueError, match="Cannot use a 3-tuple"):
            validate_dataset_split((0.6, 0.2, 0.2), "external_test")

    def test_validate_dataset_split_2_tuple_without_test_dataset(self):
        with pytest.raises(ValueError, match="requires test_dataset"):
            validate_dataset_split((0.8, 0.2), None)

    def test_validate_dataset_split_bool_passthrough(self):
        # Non-tuple values (bool) are not validated
        validate_dataset_split(True, None)  # should not raise
        validate_dataset_split(False, None)  # should not raise

    def test_validate_evaluators_valid_callable(self):
        validate_evaluators([dummy_evaluator])  # should not raise

    def test_validate_evaluators_valid_base_evaluator(self):
        class MyEval(BaseEvaluator):
            def evaluate(self, context):
                return 1.0

        validate_evaluators([MyEval(name="my_eval")])  # should not raise

    def test_validate_evaluators_empty(self):
        with pytest.raises(TypeError, match="non-empty list"):
            validate_evaluators([])

    def test_validate_evaluators_non_callable(self):
        with pytest.raises(TypeError, match="callable function"):
            validate_evaluators(["not_a_function"])

    def test_validate_evaluators_missing_params(self):
        def bad_eval(input_data):
            pass

        with pytest.raises(TypeError, match="parameters"):
            validate_evaluators([bad_eval])


# ===========================================================================
# 2. load_optimization_system_prompt
# ===========================================================================


class TestLoadOptimizationSystemPrompt:
    def test_loads_template(self):
        result = load_optimization_system_prompt({})
        assert result
        assert "prompt engineering expert" in result

    def test_injects_output_format(self):
        config = {"evaluation_output_format": '{"key": "value"}'}
        result = load_optimization_system_prompt(config)
        assert "Prompt Output Format Requirements" in result

    def test_no_output_format(self):
        result = load_optimization_system_prompt({})
        assert "{{STRUCTURE_PLACEHOLDER}}" not in result

    def test_adds_model_name(self):
        config: ConfigType = {"model_name": "gpt-4o"}
        result = load_optimization_system_prompt(config)
        assert "gpt-4o" in result
        assert "evaluation model" in result

    def test_adds_tip(self):
        random.seed(42)
        result = load_optimization_system_prompt({})
        assert "**TIP:" in result
        # Verify it contains an actual tip value
        found = any(tip in result for tip in TIPS.values())
        assert found


# ===========================================================================
# 3. OptimizationIteration
# ===========================================================================


class TestOptimizationIteration:
    def _make_iteration(self, **overrides):
        defaults = {
            "iteration": 1,
            "current_prompt": "current prompt",
            "current_results": _make_experiment_result(),
            "optimization_task": dummy_optimization_task,
            "config": {"prompt": "current prompt"},
            "labelization_function": dummy_labelization,
        }
        defaults.update(overrides)
        return OptimizationIteration(**defaults)

    def test_run_returns_improved_prompt(self):
        it = self._make_iteration(optimization_task=lambda system_prompt, user_prompt, config: "better prompt")
        result = it.run()
        assert result == "better prompt"

    def test_run_fallback_on_empty(self):
        it = self._make_iteration(optimization_task=lambda system_prompt, user_prompt, config: "")
        result = it.run()
        assert result == "current prompt"

    def test_run_fallback_on_exception(self):
        def failing_task(system_prompt, user_prompt, config):
            raise RuntimeError("boom")

        it = self._make_iteration(optimization_task=failing_task)
        result = it.run()
        assert result == "current prompt"

    def test_build_user_prompt_content(self):
        it = self._make_iteration()
        user_prompt = it._build_user_prompt()
        assert "Initial Prompt:" in user_prompt
        assert "current prompt" in user_prompt

    def test_add_examples_with_labelization(self):
        it = self._make_iteration()
        rows = _make_experiment_result()["rows"]
        result = it._add_examples(rows)
        assert "Examples from Current Evaluation" in result
        assert "correct" in result

    def test_add_examples_none_labelization(self):
        it = self._make_iteration(labelization_function=None)
        rows = _make_experiment_result()["rows"]
        result = it._add_examples(rows)
        assert result == ""

    def test_load_system_prompt_delegates(self):
        it = self._make_iteration()
        with patch(
            "ddtrace.llmobs._prompt_optimization.load_optimization_system_prompt",
            return_value="mocked system prompt",
        ) as mock_load:
            result = it._load_system_prompt()
            mock_load.assert_called_once_with(it._config)
            assert result == "mocked system prompt"


# ===========================================================================
# 4. OptimizationResult
# ===========================================================================


class TestOptimizationResult:
    def _make_iterations(self, scores):
        iterations = []
        for i, score in enumerate(scores):
            data: IterationData = {
                "iteration": i,
                "prompt": f"prompt_{i}",
                "results": _make_experiment_result(score),
                "score": score,
                "experiment_url": f"https://example.com/exp/{i}",
                "summary_evaluations": {"dummy_summary_evaluator": {"value": {"accuracy": score}, "error": None}},
            }
            iterations.append(data)
        return iterations

    def test_best_prompt(self):
        iterations = self._make_iterations([0.3, 0.8, 0.5])
        result = OptimizationResult("test", "initial", iterations, best_iteration=1)
        assert result.best_prompt == "prompt_1"

    def test_best_score(self):
        iterations = self._make_iterations([0.3, 0.8, 0.5])
        result = OptimizationResult("test", "initial", iterations, best_iteration=1)
        assert result.best_score == 0.8

    def test_best_experiment_url(self):
        iterations = self._make_iterations([0.3, 0.8])
        result = OptimizationResult("test", "initial", iterations, best_iteration=1)
        assert result.best_experiment_url == "https://example.com/exp/1"

    def test_best_prompt_fallback_empty(self):
        result = OptimizationResult("test", "initial", [], best_iteration=0)
        assert result.best_prompt == "initial"

    def test_best_prompt_fallback_out_of_range(self):
        iterations = self._make_iterations([0.5])
        result = OptimizationResult("test", "initial", iterations, best_iteration=99)
        assert result.best_prompt == "initial"

    def test_best_score_fallback(self):
        result = OptimizationResult("test", "initial", [], best_iteration=0)
        assert result.best_score is None

    def test_total_iterations(self):
        iterations = self._make_iterations([0.3, 0.5, 0.8])
        result = OptimizationResult("test", "initial", iterations, best_iteration=2)
        assert result.total_iterations == 3

    def test_test_score_with_phase(self):
        iterations = self._make_iterations([0.5])
        test_phase = TestPhaseResult(results=_make_experiment_result(0.9), score=0.9, experiment_url="https://test/url")
        result = OptimizationResult("test", "initial", iterations, best_iteration=0, test_phase=test_phase)
        assert result.test_score == 0.9

    def test_test_experiment_url_with_phase(self):
        iterations = self._make_iterations([0.5])
        test_phase = TestPhaseResult(results=_make_experiment_result(0.9), score=0.9, experiment_url="https://test/url")
        result = OptimizationResult("test", "initial", iterations, best_iteration=0, test_phase=test_phase)
        assert result.test_experiment_url == "https://test/url"

    def test_test_results_with_phase(self):
        exp = _make_experiment_result(0.9)
        test_phase = TestPhaseResult(results=exp, score=0.9, experiment_url="https://test/url")
        iterations = self._make_iterations([0.5])
        result = OptimizationResult("test", "initial", iterations, best_iteration=0, test_phase=test_phase)
        assert result.test_results is exp

    def test_test_score_without_phase(self):
        iterations = self._make_iterations([0.5])
        result = OptimizationResult("test", "initial", iterations, best_iteration=0)
        assert result.test_score is None
        assert result.test_experiment_url is None
        assert result.test_results is None

    def test_get_history(self):
        iterations = self._make_iterations([0.3, 0.8])
        result = OptimizationResult("test", "initial", iterations, best_iteration=1)
        assert result.get_history() is iterations

    def test_get_score_history(self):
        iterations = self._make_iterations([0.3, 0.8, 0.5])
        result = OptimizationResult("test", "initial", iterations, best_iteration=1)
        assert result.get_score_history() == [0.3, 0.8, 0.5]

    def test_get_prompt_history(self):
        iterations = self._make_iterations([0.3, 0.8])
        result = OptimizationResult("test", "initial", iterations, best_iteration=1)
        assert result.get_prompt_history() == ["prompt_0", "prompt_1"]

    def test_summary_basic(self):
        iterations = self._make_iterations([0.3, 0.8])
        result = OptimizationResult("test", "initial", iterations, best_iteration=1)
        s = result.summary()
        assert "test" in s
        assert "0.8000" in s
        assert "BEST" in s

    def test_summary_with_test_phase(self):
        iterations = self._make_iterations([0.5])
        test_phase = TestPhaseResult(results=_make_experiment_result(0.9), score=0.9, experiment_url="https://test/url")
        result = OptimizationResult("test", "initial", iterations, best_iteration=0, test_phase=test_phase)
        s = result.summary()
        assert "Test score: 0.9000" in s
        assert "https://test/url" in s


# ===========================================================================
# 5. PromptOptimization Init
# ===========================================================================


class TestPromptOptimizationInit:
    def test_valid_metaprompting(self):
        opt = _make_prompt_optimization(method="metaprompting")
        assert opt._method == "metaprompting"

    def test_valid_gepa(self):
        opt = _make_prompt_optimization(method="gepa")
        assert opt._method == "gepa"

    def test_invalid_method(self):
        with pytest.raises(ValueError, match="Unknown optimization method"):
            _make_prompt_optimization(method="invalid")

    def test_missing_config(self):
        with pytest.raises(ValueError, match="config parameter is required"):
            _make_prompt_optimization(config=None)

    def test_config_without_prompt(self):
        with pytest.raises(ValueError, match="must contain a 'prompt' key"):
            _make_prompt_optimization(config={"model_name": "gpt-4"})

    def test_split_ratios_true(self):
        opt = _make_prompt_optimization(dataset_split=True)
        assert opt._dataset_split_enabled is True
        assert opt._split_ratios == (0.6, 0.2, 0.2)

    def test_split_ratios_custom(self):
        opt = _make_prompt_optimization(dataset_split=(0.7, 0.15, 0.15))
        assert opt._split_ratios == (0.7, 0.15, 0.15)

    def test_split_ratios_2_tuple_with_test_dataset(self):
        test_ds = _make_dataset("test_ext", 3)
        opt = _make_prompt_optimization(dataset_split=(0.8, 0.2), test_dataset=test_ds)
        assert opt._split_ratios == (0.8, 0.2)
        assert opt._dataset_split_enabled is True

    def test_test_dataset_implies_split(self):
        test_ds = _make_dataset("test_ext", 3)
        opt = _make_prompt_optimization(test_dataset=test_ds)
        assert opt._dataset_split_enabled is True
        # Default 2-way split when test_dataset provided
        assert opt._split_ratios == (0.8, 0.2)


# ===========================================================================
# 6. Run Routing
# ===========================================================================


class TestRunRouting:
    def test_llmobs_not_enabled_raises(self):
        mock_llmobs = MagicMock()
        mock_llmobs.enabled = False
        opt = _make_prompt_optimization(_llmobs_instance=mock_llmobs)
        with pytest.raises(ValueError, match="LLMObs is not enabled"):
            opt.run()

    @patch.object(PromptOptimization, "_run_without_split")
    def test_metaprompting_no_split(self, mock_run):
        mock_run.return_value = MagicMock()
        opt = _make_prompt_optimization(method="metaprompting", dataset_split=False)
        opt.run()
        mock_run.assert_called_once()

    @patch.object(PromptOptimization, "_run_with_split")
    def test_metaprompting_with_split(self, mock_run):
        mock_run.return_value = MagicMock()
        opt = _make_prompt_optimization(method="metaprompting", dataset_split=True)
        opt.run()
        mock_run.assert_called_once()

    @patch.object(PromptOptimization, "_run_gepa_without_split")
    def test_gepa_no_split(self, mock_run):
        mock_run.return_value = MagicMock()
        opt = _make_prompt_optimization(method="gepa", dataset_split=False)
        opt.run()
        mock_run.assert_called_once()

    @patch.object(PromptOptimization, "_run_gepa_with_split")
    def test_gepa_with_split(self, mock_run):
        mock_run.return_value = MagicMock()
        opt = _make_prompt_optimization(method="gepa", dataset_split=True)
        opt.run()
        mock_run.assert_called_once()


# ===========================================================================
# 7. _run_without_split (metaprompting)
# ===========================================================================


class TestRunWithoutSplit:
    @patch.object(OptimizationIteration, "run", return_value="improved")
    @patch.object(PromptOptimization, "_run_experiment")
    def test_runs_baseline_plus_iterations(self, mock_exp, mock_iter_run):
        scores = [0.5, 0.6, 0.7]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(max_iterations=2)
        result = opt._run_without_split(jobs=1)
        assert result.total_iterations == 3  # baseline + 2
        assert result.best_score == 0.7

    @patch.object(OptimizationIteration, "run", return_value="improved")
    @patch.object(PromptOptimization, "_run_experiment")
    def test_best_iteration_selected(self, mock_exp, mock_iter_run):
        # Baseline scores highest
        scores = [0.9, 0.3, 0.5]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(max_iterations=2)
        result = opt._run_without_split(jobs=1)
        assert result.best_iteration == 0
        assert result.best_score == 0.9

    @patch.object(OptimizationIteration, "run", return_value="improved")
    @patch.object(PromptOptimization, "_run_experiment")
    def test_stopping_condition_halts_early(self, mock_exp, mock_iter_run):
        scores = [0.3, 0.95]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(
            max_iterations=5,
            stopping_condition=lambda evals: True,  # always stop
        )
        result = opt._run_without_split(jobs=1)
        # Baseline + 1 iteration (stopped after first)
        assert result.total_iterations == 2

    @patch.object(OptimizationIteration, "run", return_value="improved_v2")
    @patch.object(PromptOptimization, "_run_experiment")
    def test_optimizes_from_best_prompt(self, mock_exp, mock_iter_run):
        """Verify OptimizationIteration is constructed with the best prompt so far."""
        scores = [0.3, 0.8, 0.5]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect

        captured_prompts = []
        original_init = OptimizationIteration.__init__

        def tracking_init(self_iter, *args, **kwargs):
            original_init(self_iter, *args, **kwargs)
            captured_prompts.append(self_iter.current_prompt)

        with patch.object(OptimizationIteration, "__init__", tracking_init):
            opt = _make_prompt_optimization(max_iterations=2)
            opt._run_without_split(jobs=1)

        # First iteration: starts with initial prompt
        assert captured_prompts[0] == "initial prompt"
        # Second iteration: starts with best (iteration 1 had 0.8 > baseline 0.3)
        assert captured_prompts[1] == "improved_v2"


# ===========================================================================
# 8. _run_with_split (metaprompting)
# ===========================================================================


class TestRunWithSplit:
    @patch.object(OptimizationIteration, "run", return_value="improved")
    @patch.object(PromptOptimization, "_run_experiment")
    @patch.object(PromptOptimization, "_create_split_datasets")
    def test_creates_splits_and_test_phase(self, mock_split, mock_exp, mock_iter_run):
        train_ds = _make_dataset("train", 6)
        valid_ds = _make_dataset("valid", 2)
        test_ds = _make_dataset("test", 2)
        mock_split.return_value = (train_ds, valid_ds, test_ds)

        scores = [0.5, 0.5, 0.6, 0.6, 0.9]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(max_iterations=1, dataset_split=True)
        result = opt._run_with_split(jobs=1)
        assert result.test_score is not None
        assert result._test_phase is not None

    @patch.object(OptimizationIteration, "run", return_value="improved")
    @patch.object(PromptOptimization, "_run_experiment")
    @patch.object(PromptOptimization, "_create_split_datasets")
    def test_scores_from_valid_set(self, mock_split, mock_exp, mock_iter_run):
        train_ds = _make_dataset("train", 6)
        valid_ds = _make_dataset("valid", 2)
        test_ds = _make_dataset("test", 2)
        mock_split.return_value = (train_ds, valid_ds, test_ds)

        # train=0.3, valid=0.7, train=0.5, valid=0.8, test=0.9
        scores = [0.3, 0.7, 0.5, 0.8, 0.9]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(max_iterations=1, dataset_split=True)
        result = opt._run_with_split(jobs=1)
        # Best valid score is 0.8 (iteration 1)
        assert result.best_iteration == 1

    @patch.object(OptimizationIteration, "run", return_value="improved")
    @patch.object(PromptOptimization, "_run_experiment")
    @patch.object(PromptOptimization, "_create_split_datasets")
    def test_has_test_phase_result(self, mock_split, mock_exp, mock_iter_run):
        train_ds = _make_dataset("train", 6)
        valid_ds = _make_dataset("valid", 2)
        test_ds = _make_dataset("test", 2)
        mock_split.return_value = (train_ds, valid_ds, test_ds)

        scores = [0.3, 0.7, 0.5, 0.8, 0.9]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(max_iterations=1, dataset_split=True)
        result = opt._run_with_split(jobs=1)
        assert isinstance(result._test_phase, TestPhaseResult)
        assert result._test_phase.score is not None


# ===========================================================================
# 9. _create_split_datasets
# ===========================================================================


class TestCreateSplitDatasets:
    def test_default_3_way_split(self):
        opt = _make_prompt_optimization(dataset_split=True, dataset=_make_dataset(num_records=10))
        train_ds, valid_ds, test_ds = opt._create_split_datasets()
        assert len(train_ds) == 6
        assert len(valid_ds) == 2
        assert len(test_ds) == 2

    def test_2_way_with_test_dataset(self):
        ext_test = _make_dataset("ext_test", 3)
        opt = _make_prompt_optimization(
            dataset_split=(0.8, 0.2), test_dataset=ext_test, dataset=_make_dataset(num_records=10)
        )
        train_ds, valid_ds, test_ds = opt._create_split_datasets()
        assert len(train_ds) == 8
        assert len(valid_ds) == 2
        assert test_ds is ext_test

    def test_custom_ratios(self):
        opt = _make_prompt_optimization(dataset_split=(0.5, 0.3, 0.2), dataset=_make_dataset(num_records=10))
        train_ds, valid_ds, test_ds = opt._create_split_datasets()
        assert len(train_ds) == 5
        assert len(valid_ds) == 3
        assert len(test_ds) == 2

    def test_reproducible(self):
        ds = _make_dataset(num_records=10)
        opt1 = _make_prompt_optimization(dataset_split=True, dataset=ds)
        opt2 = _make_prompt_optimization(dataset_split=True, dataset=ds)
        train1, valid1, test1 = opt1._create_split_datasets()
        train2, valid2, test2 = opt2._create_split_datasets()
        assert [r["record_id"] for r in train1] == [r["record_id"] for r in train2]
        assert [r["record_id"] for r in valid1] == [r["record_id"] for r in valid2]
        assert [r["record_id"] for r in test1] == [r["record_id"] for r in test2]

    def test_too_few_records_raises(self):
        opt = _make_prompt_optimization(dataset_split=True, dataset=_make_dataset(num_records=2))
        with pytest.raises(ValueError, match="too few for splitting"):
            opt._create_split_datasets()


# ===========================================================================
# 10. _to_numeric_score
# ===========================================================================


class TestToNumericScore:
    @pytest.mark.parametrize(
        "input_val,expected",
        [
            (0.75, 0.75),
            (1, 1.0),
            (True, 1.0),
            (False, 0.0),
            ("pass", 1.0),
            ("correct", 1.0),
            ("true_positive", 1.0),
            ("true_negative", 1.0),
            ("incorrect", 0.0),
            ("PASS", 1.0),
            ("fail", 0.0),
        ],
    )
    def test_basic_types(self, input_val, expected):
        from ddtrace.llmobs._optimizers.gepa_strategy import LLMObsGEPAAdapter

        assert LLMObsGEPAAdapter._to_numeric_score(input_val) == expected

    def test_evaluator_result(self):
        from ddtrace.llmobs._optimizers.gepa_strategy import LLMObsGEPAAdapter

        er = EvaluatorResult(value=0.8)
        assert LLMObsGEPAAdapter._to_numeric_score(er) == 0.8

    def test_dict_with_value(self):
        from ddtrace.llmobs._optimizers.gepa_strategy import LLMObsGEPAAdapter

        assert LLMObsGEPAAdapter._to_numeric_score({"value": 0.9}) == 0.9

    def test_unexpected_type(self):
        from ddtrace.llmobs._optimizers.gepa_strategy import LLMObsGEPAAdapter

        assert LLMObsGEPAAdapter._to_numeric_score([1, 2, 3]) == 0.0


# ===========================================================================
# 11. GEPA Adapter Helpers
# ===========================================================================


class TestGEPAAdapterHelpers:
    def _make_adapter(self, **overrides):
        from ddtrace.llmobs._optimizers.gepa_strategy import LLMObsGEPAAdapter

        defaults = {
            "task": dummy_task,
            "evaluators": [dummy_evaluator],
            "optimization_task": dummy_optimization_task,
            "config": {"prompt": "test prompt"},
        }
        defaults.update(overrides)
        return LLMObsGEPAAdapter(**defaults)

    def test_build_feedback_with_expected(self):
        from ddtrace.llmobs._optimizers.gepa_strategy import TrajectoryRecord

        adapter = self._make_adapter()
        traj: TrajectoryRecord = {
            "input_data": {"q": "hello"},
            "expected_output": "world",
            "output": "wrong",
            "evaluations": {"accuracy": 0.0},
            "score": 0.0,
        }
        feedback = adapter._build_feedback(traj)
        assert "Expected Output: world" in feedback
        assert "Score: 0.00" in feedback

    def test_build_feedback_no_expected(self):
        from ddtrace.llmobs._optimizers.gepa_strategy import TrajectoryRecord

        adapter = self._make_adapter()
        traj: TrajectoryRecord = {
            "input_data": {"q": "hello"},
            "expected_output": None,
            "output": "result",
            "evaluations": {},
            "score": 0.5,
        }
        feedback = adapter._build_feedback(traj)
        assert "Expected Output" not in feedback
        assert "Score: 0.50" in feedback

    def test_build_feedback_with_reasoning(self):
        adapter = self._make_adapter()
        traj = {
            "input_data": {"q": "hello"},
            "expected_output": "world",
            "output": "wrong",
            "evaluations": {
                "my_eval": {"value": 0.0, "reasoning": "totally wrong"},
            },
            "score": 0.0,
        }
        feedback = adapter._build_feedback(traj)
        assert "my_eval reasoning: totally wrong" in feedback

    def test_build_user_prompt_from_reflective(self):
        adapter = self._make_adapter()
        entries = [
            {"Inputs": {"q": "test"}, "Generated Outputs": "out", "Feedback": "good"},
        ]
        result = adapter._build_user_prompt_from_reflective("current prompt", entries)
        assert "Initial Prompt:" in result
        assert "current prompt" in result
        assert "Example 1" in result

    def test_build_user_prompt_from_reflective_empty(self):
        adapter = self._make_adapter()
        result = adapter._build_user_prompt_from_reflective("current prompt", [])
        assert "Initial Prompt:" in result
        assert "Example" not in result

    def test_dataset_to_gepa_format(self):
        from ddtrace.llmobs._optimizers.gepa_strategy import LLMObsGEPAAdapter

        ds = _make_dataset(num_records=3)
        records = LLMObsGEPAAdapter._dataset_to_gepa_format(ds)
        assert len(records) == 3
        assert "input_data" in records[0]
        assert "expected_output" in records[0]
        assert "metadata" in records[0]

    def test_dataset_to_gepa_format_empty(self):
        from ddtrace.llmobs._optimizers.gepa_strategy import LLMObsGEPAAdapter

        ds = _make_dataset(num_records=0)
        records = LLMObsGEPAAdapter._dataset_to_gepa_format(ds)
        assert records == []


# ===========================================================================
# 12. GEPA Adapter evaluate
# ===========================================================================


class TestGEPAAdapterEvaluate:
    def _make_adapter(self, **overrides):
        from ddtrace.llmobs._optimizers.gepa_strategy import LLMObsGEPAAdapter

        defaults = {
            "task": dummy_task,
            "evaluators": [dummy_evaluator],
            "optimization_task": dummy_optimization_task,
            "config": {"prompt": "test prompt"},
        }
        defaults.update(overrides)
        return LLMObsGEPAAdapter(**defaults)

    def _mock_gepa_module(self):
        """Create a mock gepa module with EvaluationBatch."""
        mock_module = MagicMock()

        class FakeEvaluationBatch:
            def __init__(self, outputs, scores, trajectories=None):
                self.outputs = outputs
                self.scores = scores
                self.trajectories = trajectories

        mock_module.EvaluationBatch = FakeEvaluationBatch
        return mock_module

    def test_runs_task_and_evaluators(self):
        mock_gepa = self._mock_gepa_module()
        with patch.dict(sys.modules, {"gepa": mock_gepa}):
            adapter = self._make_adapter()
            batch = [
                {"input_data": {"answer": "a0"}, "expected_output": {"answer": "a0"}},
                {"input_data": {"answer": "a1"}, "expected_output": {"answer": "wrong"}},
            ]
            result = adapter.evaluate(batch, {"system_prompt": "test"}, capture_traces=False)
            assert len(result.outputs) == 2
            assert len(result.scores) == 2
            assert result.trajectories is None

    def test_capture_traces(self):
        mock_gepa = self._mock_gepa_module()
        with patch.dict(sys.modules, {"gepa": mock_gepa}):
            adapter = self._make_adapter()
            batch = [{"input_data": {"answer": "a0"}, "expected_output": {"answer": "a0"}}]
            result = adapter.evaluate(batch, {"system_prompt": "test"}, capture_traces=True)
            assert result.trajectories is not None
            assert len(result.trajectories) == 1

    def test_task_exception_returns_none_output(self):
        def failing_task(input_data, config):
            raise RuntimeError("boom")

        mock_gepa = self._mock_gepa_module()
        with patch.dict(sys.modules, {"gepa": mock_gepa}):
            adapter = self._make_adapter(task=failing_task)
            batch = [{"input_data": {"q": "test"}, "expected_output": "answer"}]
            result = adapter.evaluate(batch, {"system_prompt": "test"}, capture_traces=False)
            assert result.outputs[0] is None
            assert result.scores[0] == 0.0

    def test_candidate_prompt_injected(self):
        captured_configs = []

        def tracking_task(input_data, config):
            captured_configs.append(dict(config))
            return input_data

        mock_gepa = self._mock_gepa_module()
        with patch.dict(sys.modules, {"gepa": mock_gepa}):
            adapter = self._make_adapter(task=tracking_task)
            batch = [{"input_data": {"q": "test"}, "expected_output": "answer"}]
            adapter.evaluate(batch, {"system_prompt": "injected_prompt"}, capture_traces=False)
            assert captured_configs[0]["prompt"] == "injected_prompt"


# ===========================================================================
# 13. GEPA Adapter propose_new_texts
# ===========================================================================


class TestGEPAAdapterProposeNewTexts:
    def _make_adapter(self, **overrides):
        from ddtrace.llmobs._optimizers.gepa_strategy import LLMObsGEPAAdapter

        defaults = {
            "task": dummy_task,
            "evaluators": [dummy_evaluator],
            "optimization_task": dummy_optimization_task,
            "config": {"prompt": "test prompt"},
        }
        defaults.update(overrides)
        return LLMObsGEPAAdapter(**defaults)

    @patch("ddtrace.llmobs._prompt_optimization.load_optimization_system_prompt", return_value="sys prompt")
    def test_success(self, mock_load):
        def opt_task(system_prompt, user_prompt, config):
            return "new prompt"

        adapter = self._make_adapter(optimization_task=opt_task)
        result = adapter.propose_new_texts({"system_prompt": "old"}, {"system_prompt": []}, ["system_prompt"])
        assert result == {"system_prompt": "new prompt"}

    @patch("ddtrace.llmobs._prompt_optimization.load_optimization_system_prompt", return_value="sys prompt")
    def test_empty_keeps_current(self, mock_load):
        def opt_task(system_prompt, user_prompt, config):
            return ""

        adapter = self._make_adapter(optimization_task=opt_task)
        result = adapter.propose_new_texts({"system_prompt": "old"}, {"system_prompt": []}, ["system_prompt"])
        assert result == {"system_prompt": "old"}

    @patch("ddtrace.llmobs._prompt_optimization.load_optimization_system_prompt", return_value="sys prompt")
    def test_exception_keeps_current(self, mock_load):
        def opt_task(system_prompt, user_prompt, config):
            raise RuntimeError("fail")

        adapter = self._make_adapter(optimization_task=opt_task)
        result = adapter.propose_new_texts({"system_prompt": "old"}, {"system_prompt": []}, ["system_prompt"])
        assert result == {"system_prompt": "old"}


# ===========================================================================
# 14. _run_gepa_core
# ===========================================================================


class TestRunGEPACore:
    def test_success(self):
        mock_gepa = MagicMock()
        mock_result = MagicMock()
        mock_result.best_candidate = {"system_prompt": "GEPA optimized"}
        mock_gepa.optimize.return_value = mock_result

        with patch.dict(sys.modules, {"gepa": mock_gepa}):
            opt = _make_prompt_optimization(method="gepa")
            ds = _make_dataset()
            result = opt._run_gepa_core(ds, ds)
            assert result == "GEPA optimized"

    def test_gepa_not_installed(self):
        # Ensure gepa is not available
        with patch.dict(sys.modules, {"gepa": None}):
            opt = _make_prompt_optimization(method="gepa")
            ds = _make_dataset()
            with pytest.raises(ImportError, match="gepa package is required"):
                opt._run_gepa_core(ds, ds)

    def test_optimize_raises_falls_back(self):
        mock_gepa = MagicMock()
        mock_gepa.optimize.side_effect = RuntimeError("GEPA crashed")

        with patch.dict(sys.modules, {"gepa": mock_gepa}):
            opt = _make_prompt_optimization(method="gepa")
            ds = _make_dataset()
            result = opt._run_gepa_core(ds, ds)
            assert result == "initial prompt"  # fallback to initial

    def test_config_params_forwarded(self):
        mock_gepa = MagicMock()
        mock_result = MagicMock()
        mock_result.best_candidate = {"system_prompt": "optimized"}
        mock_gepa.optimize.return_value = mock_result

        with patch.dict(sys.modules, {"gepa": mock_gepa}):
            opt = _make_prompt_optimization(
                method="gepa",
                config={
                    "prompt": "initial prompt",
                    "max_metric_calls": 200,
                    "gepa_seed": 99,
                    "candidate_selection_strategy": "elite",
                },
            )
            ds = _make_dataset()
            opt._run_gepa_core(ds, ds)

            call_kwargs = mock_gepa.optimize.call_args[1]
            assert call_kwargs["max_metric_calls"] == 200
            assert call_kwargs["seed"] == 99
            assert call_kwargs["candidate_selection_strategy"] == "elite"


# ===========================================================================
# 15. _run_gepa_without_split
# ===========================================================================


class TestRunGEPAWithoutSplit:
    @patch.object(PromptOptimization, "_run_gepa_core", return_value="optimized prompt")
    @patch.object(PromptOptimization, "_run_experiment")
    def test_baseline_and_final(self, mock_exp, mock_gepa_core):
        scores = [0.5, 0.8]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(method="gepa")
        result = opt._run_gepa_without_split(jobs=1)
        assert result.total_iterations == 2
        assert result._test_phase is None

    @patch.object(PromptOptimization, "_run_gepa_core", return_value="optimized prompt")
    @patch.object(PromptOptimization, "_run_experiment")
    def test_best_iteration_selected(self, mock_exp, mock_gepa_core):
        # Baseline better than optimized
        scores = [0.9, 0.3]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(method="gepa")
        result = opt._run_gepa_without_split(jobs=1)
        assert result.best_iteration == 0

    @patch.object(PromptOptimization, "_run_gepa_core", return_value="optimized prompt")
    @patch.object(PromptOptimization, "_run_experiment")
    def test_optimized_better(self, mock_exp, mock_gepa_core):
        scores = [0.3, 0.9]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(method="gepa")
        result = opt._run_gepa_without_split(jobs=1)
        assert result.best_iteration == 1
        assert result.best_prompt == "optimized prompt"


# ===========================================================================
# 16. _run_gepa_with_split
# ===========================================================================


class TestRunGEPAWithSplit:
    @patch.object(PromptOptimization, "_run_gepa_core", return_value="optimized prompt")
    @patch.object(PromptOptimization, "_run_experiment")
    @patch.object(PromptOptimization, "_create_split_datasets")
    def test_creates_splits_and_test(self, mock_split, mock_exp, mock_gepa_core):
        train_ds = _make_dataset("train", 6)
        valid_ds = _make_dataset("valid", 2)
        test_ds = _make_dataset("test", 2)
        mock_split.return_value = (train_ds, valid_ds, test_ds)

        # baseline_valid, final_valid, test
        scores = [0.5, 0.8, 0.75]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(method="gepa", dataset_split=True)
        result = opt._run_gepa_with_split(jobs=1)
        assert result.total_iterations == 2
        assert result._test_phase is not None
        assert result._test_phase.score is not None

    @patch.object(PromptOptimization, "_run_gepa_core", return_value="optimized prompt")
    @patch.object(PromptOptimization, "_run_experiment")
    @patch.object(PromptOptimization, "_create_split_datasets")
    def test_test_runs_with_best_prompt(self, mock_split, mock_exp, mock_gepa_core):
        train_ds = _make_dataset("train", 6)
        valid_ds = _make_dataset("valid", 2)
        test_ds = _make_dataset("test", 2)
        mock_split.return_value = (train_ds, valid_ds, test_ds)

        scores = [0.5, 0.8, 0.75]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(method="gepa", dataset_split=True)
        result = opt._run_gepa_with_split(jobs=1)
        # Final iteration is better (0.8 > 0.5), so best is iteration 1
        assert result.best_iteration == 1
        # Test phase runs with best prompt
        test_call = mock_exp.call_args_list[-1]
        assert test_call[0][1] == "optimized prompt"

    @patch.object(PromptOptimization, "_run_gepa_core", return_value="optimized prompt")
    @patch.object(PromptOptimization, "_run_experiment")
    @patch.object(PromptOptimization, "_create_split_datasets")
    def test_has_test_phase(self, mock_split, mock_exp, mock_gepa_core):
        train_ds = _make_dataset("train", 6)
        valid_ds = _make_dataset("valid", 2)
        test_ds = _make_dataset("test", 2)
        mock_split.return_value = (train_ds, valid_ds, test_ds)

        scores = [0.5, 0.8, 0.9]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(method="gepa", dataset_split=True)
        result = opt._run_gepa_with_split(jobs=1)
        assert isinstance(result._test_phase, TestPhaseResult)

    @patch.object(PromptOptimization, "_run_gepa_core", return_value="optimized prompt")
    @patch.object(PromptOptimization, "_run_experiment")
    @patch.object(PromptOptimization, "_create_split_datasets")
    def test_baseline_wins(self, mock_split, mock_exp, mock_gepa_core):
        train_ds = _make_dataset("train", 6)
        valid_ds = _make_dataset("valid", 2)
        test_ds = _make_dataset("test", 2)
        mock_split.return_value = (train_ds, valid_ds, test_ds)

        # baseline_valid=0.9, final_valid=0.3, test=0.85
        scores = [0.9, 0.3, 0.85]
        call_idx = {"i": 0}

        def side_effect(*args, **kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return _make_experiment_result(scores[idx]), f"https://exp/{idx}"

        mock_exp.side_effect = side_effect
        opt = _make_prompt_optimization(method="gepa", dataset_split=True)
        result = opt._run_gepa_with_split(jobs=1)
        assert result.best_iteration == 0
        # Test runs with baseline (initial) prompt
        test_call = mock_exp.call_args_list[-1]
        assert test_call[0][1] == "initial prompt"
