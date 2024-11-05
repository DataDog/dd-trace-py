import itertools
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Union

from ddtrace.llmobs import Dataset


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
            # ... more data entries ...
        ],
    )

    assert dataset.name == "geography-dataset"
    assert dataset[0] == {"input": {"prompt": "capital of France?"}, "expected_output": {"response": "Paris"}}
