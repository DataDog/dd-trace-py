"""
Test coverage ideas:
- Define task and evaluator wrong
- Define experiment wrong
- Experiments with failures
- Eval failures
- Test workflows for re-evals and publishing results
"""

import itertools
import os
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Union

import pytest
import vcr

from ddtrace.llmobs import Dataset


# Define a function to scrub the headers you want to remove
def scrub_response_headers(response):
    # Remove specific headers
    headers_to_remove = ["content-security-policy"]
    for header in headers_to_remove:
        response["headers"].pop(header, None)
    return response


@pytest.fixture
def experiments_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/experiments"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["DD-API-KEY", "DD-APPLICATION-KEY", "Openai-Api-Key", "Authorization"],
        before_record_response=scrub_response_headers,
    )


def parametrize(**param_dict: Dict[str, Union[Any, List[Any]]]) -> Callable:
    """Decorator that creates multiple versions by combining all parameter values.

    Args:
        **param_dict: Dictionary of parameter names and their possible values.
                     Values can be single items or lists of possible values.
    Returns:
        List[Any]: List of results from calling the decorated function with each parameter combination
    """

    def decorator(func):
        # Convert single values to lists
        processed_params = {
            name: [val] if not isinstance(val, (list, tuple)) else val for name, val in param_dict.items()
        }

        # Generate all combinations of parameters
        param_names = list(processed_params.keys())
        param_values = [processed_params[name] for name in param_names]
        param_combinations = [dict(zip(param_names, combo)) for combo in itertools.product(*param_values)]

        # Return list of results from calling function with each combination
        return [func(**params) for params in param_combinations]

    return decorator


def test_create_dataset():
    dataset = Dataset(
        name="geography-dataset",
        data=[
            {"input": {"prompt": "capital of France?"}, "expected_output": {"response": "Paris"}},
            {"input": {"prompt": "capital of Germany?"}, "expected_output": {"response": "Berlin"}},
            {"input": {"prompt": "capital of Japan?"}, "expected_output": {"response": "Tokyo"}},
            {"input": {"prompt": "capital of Canada?"}, "expected_output": {"response": "Ottawa"}},
        ],
    )
    assert dataset.name == "geography-dataset"
    assert dataset[0] == {"input": {"prompt": "capital of France?"}, "expected_output": {"response": "Paris"}}


def test_dataset_pull(experiments_vcr):
    with experiments_vcr.use_cassette("test_dataset_pull.yaml"):
        dataset = Dataset.from_datadog("meal-calorie-dataset-multilingual-3")
    assert len(dataset) > 0
    assert isinstance(dataset[0], dict)
    assert "input" in dataset[0]
    assert "expected_output" in dataset[0]


def test_dataset_pull_dne(experiments_vcr):
    with experiments_vcr.use_cassette("test_dataset_pull_dne.yaml"):
        with pytest.raises(ValueError):
            Dataset.from_datadog("dataset-does-not-exist")
