"""Module for IAST sanitizers that apply security marks to function return values.

Sanitizers are functions that clean/escape their inputs to prevent security issues.
If a sanitizer returns a value, we mark that value as secure for specific vulnerability types.
"""

from typing import Any
from typing import Callable
from typing import Sequence

from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges


def create_sanitizer(
    vulnerability_type: VulnerabilityType, wrapped: Callable, instance: Any, args: Sequence, kwargs: dict
) -> Callable:
    """Create a sanitizer function wrapper that marks return values as secure for a specific vulnerability type."""
    # Apply the sanitizer function
    result = wrapped(*args, **kwargs)

    # If result is a string, mark it as secure
    ranges = get_tainted_ranges(result)
    if ranges:
        for _range in ranges:
            _range.add_secure_mark(vulnerability_type)

    return result


def secure_filename_sanitizer(wrapped: Callable, instance: Any, args: Sequence, kwargs: dict) -> Any:
    """Sanitizer for werkzeug.utils.secure_filename that marks filenames as safe from path traversal.

    Args:
        wrapped: The original secure_filename function
        instance: The instance (None for module functions)
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        The sanitized filename
    """
    return create_sanitizer(VulnerabilityType.PATH_TRAVERSAL, wrapped, instance, args, kwargs)


def sql_quote_sanitizer(wrapped: Callable, instance: Any, args: Sequence, kwargs: dict) -> Any:
    """Sanitizer for SQL quoting functions that mark output as safe from SQL injection.

    Args:
        wrapped: The original quote function
        instance: The instance (None for module functions)
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        The quoted SQL value
    """
    return create_sanitizer(VulnerabilityType.SQL_INJECTION, wrapped, instance, args, kwargs)


def command_quote_sanitizer(wrapped: Callable, instance: Any, args: Sequence, kwargs: dict) -> Any:
    """Sanitizer for shell command quoting functions that mark output as safe from command injection.

    Args:
        wrapped: The original quote function
        instance: The instance (None for module functions)
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        The quoted shell command
    """
    return create_sanitizer(VulnerabilityType.COMMAND_INJECTION, wrapped, instance, args, kwargs)
