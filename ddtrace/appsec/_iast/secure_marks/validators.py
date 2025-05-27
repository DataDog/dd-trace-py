"""Module for IAST validators that apply security marks to function arguments.

Validators are functions that check their input arguments for security issues.
If a validator approves an input, we mark that input as secure for specific vulnerability types.
"""

from typing import Any
from typing import Callable
from typing import Sequence

from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges


def create_validator(
    vulnerability_type: VulnerabilityType, wrapped: Callable, instance: Any, args: Sequence, kwargs: dict
) -> Any:
    """Create a validator function wrapper that marks arguments as secure for a specific vulnerability type."""
    # Apply the validator function
    result = wrapped(*args, **kwargs)
    for arg in args:
        if isinstance(arg, str):
            ranges = get_tainted_ranges(arg)
            if ranges:
                for _range in ranges:
                    _range.add_secure_mark(vulnerability_type)

    for arg in kwargs.values():
        if isinstance(arg, str):
            ranges = get_tainted_ranges(arg)
            if ranges:
                for _range in ranges:
                    _range.add_secure_mark(vulnerability_type)

    return result


def path_traversal_validator(wrapped: Callable, instance: Any, args: Sequence, kwargs: dict) -> bool:
    """Validator for secure filename functions.

    Args:
        wrapped: The original validator function
        instance: The instance the function is bound to (if any)
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        True if validation passed, False otherwise
    """
    return create_validator(VulnerabilityType.PATH_TRAVERSAL, wrapped, instance, args, kwargs)


def sqli_validator(wrapped: Callable, instance: Any, args: Sequence, kwargs: dict) -> bool:
    """Validator for SQL quoting functions.

    Args:
        wrapped: The original validator function
        instance: The instance the function is bound to (if any)
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        True if validation passed, False otherwise
    """
    return create_validator(VulnerabilityType.SQL_INJECTION, wrapped, instance, args, kwargs)


def cmdi_validator(wrapped: Callable, instance: Any, args: Sequence, kwargs: dict) -> bool:
    """Validator for command quoting functions.

    Args:
        wrapped: The original validator function
        instance: The instance the function is bound to (if any)
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        True if validation passed, False otherwise
    """
    return create_validator(VulnerabilityType.COMMAND_INJECTION, wrapped, instance, args, kwargs)


def unvalidated_redirect_validator(wrapped: Callable, instance: Any, args: Sequence, kwargs: dict) -> bool:
    """Validator for unvalidated redirect functions.

    Args:
        wrapped: The original validator function
        instance: The instance the function is bound to (if any)
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        True if validation passed, False otherwise
    """
    return create_validator(VulnerabilityType.UNVALIDATED_REDIRECT, wrapped, instance, args, kwargs)
