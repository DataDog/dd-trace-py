"""Public annotations for Datadog Test Optimization."""

from typing import Any
from typing import Callable
from typing import TypeVar


_TestFunction = TypeVar("_TestFunction", bound=Callable[..., Any])


def prevent_failure_suppression(test: _TestFunction) -> _TestFunction:
    """Keep a pytest test eligible to run and its failures blocking despite Datadog quarantine or disabling.

    This decorator preserves normal test selection and expected-failure semantics.
    """
    setattr(test, "_dd_prevent_failure_suppression", True)
    return test


def no_auto_retry(test: _TestFunction) -> _TestFunction:
    """Exclude a pytest test from Datadog automatic retries, including Early Flake Detection and Attempt to Fix.

    Explicit retries configured through a pytest retry plugin remain available.
    """
    setattr(test, "_dd_no_auto_retry", True)
    return test


__all__ = ["prevent_failure_suppression", "no_auto_retry"]
