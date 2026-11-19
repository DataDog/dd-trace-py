"""Module for IAST validators that apply security marks to function arguments.

Validators are functions that check their input arguments for security issues.
If a validator approves an input, we mark that input as secure for specific vulnerability types.
"""

from collections.abc import Sequence
import functools
from typing import Any
from typing import Callable
from typing import Optional

from wrapt import PartialCallableObjectProxy

from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.secure_marks.base import add_secure_mark


def create_validator(
    vulnerability_types: list[VulnerabilityType],
    parameter_positions: Optional[list[int]],
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
        add_secure_mark(arg, vulnerability_types)
        i += 1

    keyword_positions = _keyword_positions(wrapped, instance, kwargs) if parameter_positions and kwargs else {}
    for name, arg in kwargs.items():
        # With configured positions, a keyword argument is only validated when it binds to one of them.
        if parameter_positions and keyword_positions.get(name) not in parameter_positions:
            continue
        add_secure_mark(arg, vulnerability_types)

    return result


def _keyword_positions(wrapped: Callable, instance: Any, kwargs: dict) -> dict[str, int]:
    """Map keyword parameter names to their zero-based signature positions."""
    # Top-level imports of inspect can interfere with gevent's monkey patching
    import inspect

    try:
        # A method called through its class reaches the hook partially applied to instance. wrapt 1.x reports the
        # unbound signature for it, including self, which would shift every position by one.
        if isinstance(wrapped, PartialCallableObjectProxy):
            wrapped = functools.partial(wrapped.__wrapped__, instance)
        parameters = list(inspect.signature(wrapped).parameters.values())
        if instance is None and inspect.isfunction(wrapped) and parameters and parameters[0].name in kwargs:
            # A keyword-passed receiver stays in the unbound signature. Verify its method descriptor before
            # excluding it, since ordinary functions and static methods can also have a parameter named self.
            receiver = kwargs[parameters[0].name]
            descriptor = inspect.getattr_static(type(receiver), wrapped.__name__, None)
            if inspect.isfunction(descriptor) and inspect.unwrap(descriptor) is inspect.unwrap(wrapped):
                parameters = parameters[1:]
    except Exception:
        # The validator has already run, so an unusual callable must not raise into the application.
        return {}
    return {
        param.name: position
        for position, param in enumerate(parameters)
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }


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
