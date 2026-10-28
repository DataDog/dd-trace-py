"""Public annotations for Datadog Test Optimization."""

from functools import partial
from functools import update_wrapper
from types import FunctionType
from typing import Any
from typing import Callable
from typing import TypeVar
from typing import cast


_TestFunction = TypeVar("_TestFunction", bound=Callable[..., Any])


def _annotate_test(test: _TestFunction, attribute: str) -> _TestFunction:
    # Copy function declarations so reused callbacks do not share annotation policy. Keeping the
    # original code preserves descriptors, signatures, and coroutine/generator execution semantics.
    annotated: Callable[..., Any]
    if isinstance(test, FunctionType):
        function = FunctionType(test.__code__, test.__globals__, test.__name__, test.__defaults__, test.__closure__)
        function.__kwdefaults__ = test.__kwdefaults__
        annotated = function
    else:
        annotated = partial(test)
    update_wrapper(annotated, test)
    setattr(annotated, attribute, True)
    return cast(_TestFunction, annotated)


def prevent_failure_suppression(test: _TestFunction) -> _TestFunction:
    """Keep a pytest test eligible to run and its failures blocking despite Datadog quarantine or disabling.

    This decorator preserves normal test selection and expected-failure semantics.
    """
    return _annotate_test(test, "_dd_prevent_failure_suppression")


def no_auto_retry(test: _TestFunction) -> _TestFunction:
    """Exclude a pytest test from Datadog automatic retries, including Early Flake Detection and Attempt to Fix.

    Explicit retries configured through a pytest retry plugin remain available.
    """
    return _annotate_test(test, "_dd_no_auto_retry")


__all__ = ["prevent_failure_suppression", "no_auto_retry"]
