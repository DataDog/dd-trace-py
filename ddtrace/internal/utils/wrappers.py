from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import Optional  # noqa:F401
from typing import TypeVar  # noqa:F401

import wrapt


F = TypeVar("F", bound=Callable[..., Any])


def iswrapped(obj, attr=None):
    # type: (Any, Optional[str]) -> bool
    """Returns whether an attribute is wrapped or not."""
    if attr is not None:
        obj = getattr(obj, attr, None)
    return (hasattr(obj, "__wrapped__") and isinstance(obj, wrapt.ObjectProxy)) or hasattr(obj, "__dd_wrapped__")


def unwrap(obj, attr):
    # type: (Any, str) -> None
    f = getattr(obj, attr)
    setattr(obj, attr, f.__wrapped__)
