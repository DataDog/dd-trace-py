from typing import Any
from typing import Callable
from typing import Optional
from typing import TypeVar

from ddtrace.vendor import wrapt


F = TypeVar("F", bound=Callable[..., Any])


def iswrapped(obj, attr=None):
    # type: (Any, Optional[str]) -> bool
    """Returns whether an attribute is wrapped or not."""
    if attr is not None:
        obj = getattr(obj, attr, None)
    return hasattr(obj, "__wrapped__") and isinstance(obj, wrapt.ObjectProxy)


def unwrap(obj, attr):
    # type: (Any, str) -> None
    f = getattr(obj, attr)
    setattr(obj, attr, f.__wrapped__)
