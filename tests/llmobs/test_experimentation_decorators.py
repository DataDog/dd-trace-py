import pytest
from typing import Any, Dict, Optional, Union, List

from ddtrace.llmobs.experimentation import _decorators as decorators


# Helper Functions for Testing

def simple_task_func(input: Dict[str, Union[str, Dict[str, Any]]]) -> str:
    """A simple task function that only accepts input."""
    return f"Processed: {input.get('data', '')}"


def task_func_with_config(input: Dict[str, Union[str, Dict[str, Any]]], config: Optional[Dict[str, Any]] = None) -> str:
    """A task function that accepts input and config."""
    cfg_val = config.get('setting', 'default') if config else 'default'
    return f"Processed: {input.get('data', '')} with config: {cfg_val}"


def simple_evaluator_func(
    input: Union[str, Dict[str, Any]], output: Union[str, Dict[str, Any]], expected_output: Union[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """A simple evaluator function."""
    return {"input": input, "output": output, "expected": expected_output, "score": 1.0}


def simple_summary_metric_func(outputs: List[Dict[str, Any]], evaluations: List[Dict[str, Any]]) -> float:
    """A simple summary metric function."""
    # Example: Calculate average score from evaluations
    total_score = 0
    count = 0
    for eval_item in evaluations:
        # Assume 'score' evaluator exists and produces numeric values
        score_data = eval_item.get("evaluations", {}).get("score", {})
        if score_data and score_data.get("value") is not None and score_data.get("error") is None:
            if isinstance(score_data["value"], (int, float)):
                total_score += score_data["value"]
                count += 1
    return total_score / count if count > 0 else 0.0


def summary_metric_dict_func(outputs: List[Dict[str, Any]], evaluations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summary metric returning a dictionary."""
    return {"num_outputs": len(outputs), "num_evals": len(evaluations)}


# Test Class

class TestExperimentationDecorators:
    """Tests for the @task and @evaluator decorators."""

    # @task Decorator Tests

    def test_task_decorator_basic(self):
        """Test @task on a function with only 'input'."""
        decorated_task = decorators.task(simple_task_func)
        assert callable(decorated_task)
        assert getattr(decorated_task, "_is_task", False) is True
        assert getattr(decorated_task, "_accepts_config", True) is False # Decorator should detect no 'config' param

        test_input = {"data": "test_data"}
        result = decorated_task(input=test_input)
        assert result == "Processed: test_data"

        # Calling with config should be ignored by the wrapped simple_task_func
        result_with_config = decorated_task(input=test_input, config={"setting": "ignored"})
        assert result_with_config == "Processed: test_data"

    def test_task_decorator_with_config(self):
        """Test @task on a function with 'input' and 'config'."""
        decorated_task = decorators.task(task_func_with_config)
        assert callable(decorated_task)
        assert getattr(decorated_task, "_is_task", False) is True
        assert getattr(decorated_task, "_accepts_config", False) is True # Decorator should detect 'config' param

        # Calling without config should use default/None
        test_input = {"data": "abc"}
        result_no_config = decorated_task(input=test_input)
        assert result_no_config == "Processed: abc with config: default"

        test_config = {"setting": "custom"}
        result_with_config = decorated_task(input=test_input, config=test_config)
        assert result_with_config == "Processed: abc with config: custom"

    def test_task_decorator_missing_input_param(self):
        """Test @task raises TypeError if 'input' parameter is missing."""
        def invalid_task_func(data: str): # Missing 'input' param
             return data

        with pytest.raises(TypeError, match="Task function must have an 'input' parameter."):
            decorators.task(invalid_task_func)

    def test_task_decorator_reserved_name(self):
        """Test @task raises NameError if function name is 'task'."""
        def task(input: Dict[str, Any]): # Function name 'task' is reserved
             return input

        with pytest.raises(NameError, match="Function name 'task' is reserved."):
             decorators.task(task)

    # @evaluator Decorator Tests

    def test_evaluator_decorator_basic(self):
        """Test @evaluator on a valid evaluator function."""
        decorated_evaluator = decorators.evaluator(simple_evaluator_func)
        assert callable(decorated_evaluator)
        assert getattr(decorated_evaluator, "_is_evaluator", False) is True

        test_input = "query"
        test_output = "response"
        test_expected = "expected_response"
        result = decorated_evaluator(input=test_input, output=test_output, expected_output=test_expected)
        assert result == {"input": test_input, "output": test_output, "expected": test_expected, "score": 1.0}

    def test_evaluator_decorator_missing_params(self):
        """Test @evaluator raises TypeError if required parameters are missing."""
        def invalid_eval_missing_input(output: str, expected_output: str):
             return {"score": 0.0}

        def invalid_eval_missing_output(input: str, expected_output: str):
             return {"score": 0.0}

        def invalid_eval_missing_expected(input: str, output: str):
             return {"score": 0.0}

        expected_error_msg = "Evaluator function must have parameters \\['input', 'output', 'expected_output'\\]"

        with pytest.raises(TypeError, match=expected_error_msg):
            decorators.evaluator(invalid_eval_missing_input)
        with pytest.raises(TypeError, match=expected_error_msg):
            decorators.evaluator(invalid_eval_missing_output)
        with pytest.raises(TypeError, match=expected_error_msg):
            decorators.evaluator(invalid_eval_missing_expected)

    def test_evaluator_decorator_extra_params(self):
        """Test @evaluator works correctly even with extra parameters."""
        def evaluator_with_extra(input: str, output: str, expected_output: str, extra_arg: bool = False):
            return {"score": 1.0 if not extra_arg else 0.5}

        decorated_evaluator = decorators.evaluator(evaluator_with_extra)
        assert callable(decorated_evaluator)
        assert getattr(decorated_evaluator, "_is_evaluator", False) is True

        # Call without the extra arg (should work via wrapper using default)
        result1 = decorated_evaluator(input="i", output="o", expected_output="e")
        assert result1["score"] == 1.0

        # Note: The current decorator wrapper doesn't explicitly pass extra args.
        # This test confirms the decorated function works when called with only the required args.
        # If passing extra args *through* the decorator call is needed, the decorator requires modification.

    # @summary_metric Decorator Tests

    def test_summary_metric_decorator_basic(self):
        """Test @summary_metric on a valid function."""
        decorated_summary = decorators.summary_metric(simple_summary_metric_func)
        assert callable(decorated_summary)
        assert getattr(decorated_summary, "_is_summary_metric", False) is True

        # Check it can be called (though logic runs in Experiment)
        # Provide dummy data matching signature
        outputs = [{"output": "a"}, {"output": "b"}]
        evaluations = [
            {"idx": 0, "evaluations": {"score": {"value": 1.0, "error": None}}},
            {"idx": 1, "evaluations": {"score": {"value": 0.5, "error": None}}},
        ]
        result = decorated_summary(outputs=outputs, evaluations=evaluations)
        assert result == 0.75 # (1.0 + 0.5) / 2

    def test_summary_metric_decorator_dict_return(self):
        """Test @summary_metric on a function returning a dict."""
        decorated_summary = decorators.summary_metric(summary_metric_dict_func)
        assert callable(decorated_summary)
        assert getattr(decorated_summary, "_is_summary_metric", False) is True

        outputs = [{"output": "a"}]
        evaluations = [{"idx": 0, "evaluations": {}}]
        result = decorated_summary(outputs=outputs, evaluations=evaluations)
        assert result == {"num_outputs": 1, "num_evals": 1}

    def test_summary_metric_decorator_missing_params(self):
        """Test @summary_metric raises TypeError if required parameters are missing."""
        def invalid_summary_missing_outputs(evaluations: List[Dict]):
             return 0.0
        def invalid_summary_missing_evals(outputs: List[Dict]):
             return 0.0

        expected_error_msg = "Summary metric function must have parameters \\['outputs', 'evaluations'\\]"

        with pytest.raises(TypeError, match=expected_error_msg):
            decorators.summary_metric(invalid_summary_missing_outputs)
        with pytest.raises(TypeError, match=expected_error_msg):
            decorators.summary_metric(invalid_summary_missing_evals)

    def test_summary_metric_decorator_extra_params(self):
        """Test @summary_metric works correctly even with extra parameters."""
        def summary_with_extra(outputs: List[Dict], evaluations: List[Dict], extra_arg: bool = False):
            return 1.0 if not extra_arg else 0.5

        decorated_summary = decorators.summary_metric(summary_with_extra)
        assert callable(decorated_summary)
        assert getattr(decorated_summary, "_is_summary_metric", False) is True

        # The wrapper doesn't pass extra args, but the check ensures the original func is valid
        outputs = []
        evaluations = []
        result = decorated_summary(outputs=outputs, evaluations=evaluations) # Call *decorated* func
        assert result == 1.0 # Should use default extra_arg=False
