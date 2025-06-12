"""Module for IAST validators that apply security marks to function arguments.

Validators are functions that check their input arguments for security issues.
If a validator approves an input, we mark that input as secure for specific vulnerability types.
"""

from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Sequence

from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges


def create_validator(
    vulnerability_types: List[VulnerabilityType],
    parameter_positions: Optional[List[int]],
    wrapped: Callable,
    instance: Any,
    args: Sequence,
    kwargs: dict,
) -> Any:
    """Create a validator function wrapper that marks arguments as secure for a specific vulnerability type."""
    # Apply the validator function
    result = wrapped(*args, **kwargs)
    i = 0
    for arg in args:
        if parameter_positions != [] and isinstance(parameter_positions, list):
            if i not in parameter_positions:
                i += 1
                continue
        if isinstance(arg, str):
            ranges = get_tainted_ranges(arg)
            if ranges:
                for _range in ranges:
                    for vuln_type in vulnerability_types:
                        _range.add_secure_mark(vuln_type)
        i += 1

    for arg in kwargs.values():
        if isinstance(arg, str):
            ranges = get_tainted_ranges(arg)
            if ranges:
                for _range in ranges:
                    for vuln_type in vulnerability_types:
                        _range.add_secure_mark(vuln_type)

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
    return create_validator([VulnerabilityType.PATH_TRAVERSAL], None, wrapped, instance, args, kwargs)


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
    return create_validator([VulnerabilityType.SQL_INJECTION], None, wrapped, instance, args, kwargs)


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
    return create_validator([VulnerabilityType.COMMAND_INJECTION], None, wrapped, instance, args, kwargs)


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
    return create_validator([VulnerabilityType.UNVALIDATED_REDIRECT], None, wrapped, instance, args, kwargs)


def header_injection_validator(wrapped: Callable, instance: Any, args: Sequence, kwargs: dict) -> bool:
    return create_validator([VulnerabilityType.HEADER_INJECTION], None, wrapped, instance, args, kwargs)


def ssrf_validator(wrapped: Callable, instance: Any, args: Sequence, kwargs: dict) -> bool:
    """Validator for ssrf functions.

    Args:
        wrapped: The original validator function
        instance: The instance the function is bound to (if any)
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        True if validation passed, False otherwise
    """
    return create_validator([VulnerabilityType.SSRF], None, wrapped, instance, args, kwargs)
