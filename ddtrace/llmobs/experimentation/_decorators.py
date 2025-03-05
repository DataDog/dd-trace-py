from functools import wraps
import inspect
from typing import Any, Dict, Optional, Union




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


