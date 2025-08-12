import inspect
from inspect import Parameter
from typing import Any
from typing import List


def is_cython(obj):
    """Check if an object is a Cython function or method"""

    # TODO(suo): We could split these into two functions, one for Cython
    # functions and another for Cython methods.
    # TODO(suo): There doesn't appear to be a Cython function 'type' we can
    # check against via isinstance. Please correct me if I'm wrong.
    def check_cython(x):
        return type(x).__name__ == "cython_function_or_method"

    # Check if function or method, respectively
    return check_cython(obj) or (hasattr(obj, "__func__") and check_cython(obj.__func__))


def get_signature(func: Any) -> inspect.Signature:
    """Get signature parameters.

    Support Cython functions by grabbing relevant attributes from the Cython
    function and attaching to a no-op function. This is somewhat brittle, since
    inspect may change, but given that inspect is written to a PEP, we hope
    it is relatively stable. Future versions of Python may allow overloading
    the inspect 'isfunction' and 'ismethod' functions / create ABC for Python
    functions. Until then, it appears that Cython won't do anything about
    compatability with the inspect module.

    Args:
        func: The function whose signature should be checked.

    Returns:
        A function signature object, which includes the names of the keyword
            arguments as well as their default values.

    Raises:
        TypeError: A type error if the signature is not supported
    """
    # The first condition for Cython functions, the latter for Cython instance
    # methods
    if is_cython(func):
        attrs = ["__code__", "__annotations__", "__defaults__", "__kwdefaults__"]

        if all(hasattr(func, attr) for attr in attrs):
            original_func = func

            def func():
                return

            for attr in attrs:
                setattr(func, attr, getattr(original_func, attr))
        else:
            raise TypeError(f"{func!r} is not a Python function we can process")

    return inspect.signature(func)


def extract_signature(func: Any, ignore_first: bool = False) -> List[Parameter]:
    """Extract the function signature from the function.

    Args:
        func: The function whose signature should be extracted.
        ignore_first: True if the first argument should be ignored. This should
            be used when func is a method of a class.

    Returns:
        List of Parameter objects representing the function signature.
    """
    signature_parameters = list(get_signature(func).parameters.values())

    if ignore_first:
        if len(signature_parameters) == 0:
            raise ValueError(
                "Methods must take a 'self' argument, but the " f"method '{func.__name__}' does not have one."
            )
        signature_parameters = signature_parameters[1:]

    return signature_parameters

def _sort_params_list(params_list: List[Parameter]):
    """Given a list of Parameters, if a kwargs Parameter exists,
    move it to the end of the list."""
    for i, param in enumerate(params_list):
        if param.kind == Parameter.VAR_KEYWORD:
            params_list.append(params_list.pop(i))
            break
    return params_list