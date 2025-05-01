from functools import wraps
import inspect
from typing import Any, Dict, Optional, Union, List


def task(func):
    if func.__name__ == "task":
        raise NameError("Function name 'task' is reserved. Please use a different name for your task function.")

    @wraps(func)
    def wrapper(input: Dict[str, Union[str, Dict[str, Any]]], config: Optional[Dict[str, Any]] = None) -> Any:
        # Call the original function with or without config
        if "config" in inspect.signature(func).parameters:
            return func(input, config)
        return func(input)

    # Enforce signature compliance
    sig = inspect.signature(func)
    params = sig.parameters
    if "input" not in params:
        raise TypeError("Task function must have an 'input' parameter.")
    # Set attribute to indicate whether the function accepts config
    wrapper._accepts_config = "config" in params
    wrapper._is_task = True  # Set attribute to indicate decoration
    return wrapper


def evaluator(func):
    @wraps(func)
    def wrapper(
        input: Union[str, Dict[str, Any]] = None,
        output: Union[str, Dict[str, Any]] = None,
        expected_output: Union[str, Dict[str, Any]] = None,
    ) -> Any:
        return func(input, output, expected_output)

    # Enforce signature compliance
    sig = inspect.signature(func)
    params = sig.parameters
    required_params = ["input", "output", "expected_output"]
    if not all(param in params for param in required_params):
        raise TypeError(f"Evaluator function must have parameters {required_params}.")
    wrapper._is_evaluator = True  # Set attribute to indicate decoration
    return wrapper


def summary_metric(_func=None, *, primary: bool = False):
    """
    Decorator for summary metric functions that run after all evaluations.

    Can be used as @summary_metric or @summary_metric(primary=True).

    Args:
        primary (bool, optional): Marks this metric as a primary metric for the experiment. Defaults to False.

    Summary metric functions should accept two arguments:
    - outputs (List[Dict]): The list of outputs from the experiment task run.
    - evaluations (List[Dict]): The list of evaluation results.

    The function should return either:
    - A single value (int, float, bool, str) to be pushed using the function's name as the metric name.
    - A dictionary of {metric_name: value} pairs to be pushed.
    """
    # This inner function is the actual decorator logic
    def decorator(func):
        @wraps(func)
        def wrapper(outputs: List[Dict[str, Any]], evaluations: List[Dict[str, Any]]) -> Any:
            # The actual logic runs in Experiment._run_summary_metrics
            # This wrapper primarily enforces signature and marks the function.
            return func(outputs, evaluations)

        # Enforce signature compliance
        sig = inspect.signature(func)
        params = sig.parameters
        required_params = ["outputs", "evaluations"]
        if not all(param in params for param in required_params):
            raise TypeError(f"Summary metric function must have parameters {required_params}.")

        wrapper._is_summary_metric = True  # Set attribute to indicate decoration
        wrapper._is_primary = primary  # Store the primary flag (captured from outer scope)
        return wrapper

    # Check if the decorator was used without parentheses (e.g., @summary_metric)
    if _func is not None:
        # If _func is provided, it means @summary_metric was used directly.
        # Apply the decorator logic immediately with default primary=False.
        return decorator(_func)
    # If _func is None, it means @summary_metric() or @summary_metric(primary=True) was used.
    # Return the inner 'decorator' function, which will be called with 'func' later.
    # The 'primary' value passed (or default) is captured in the closure.
    return decorator
