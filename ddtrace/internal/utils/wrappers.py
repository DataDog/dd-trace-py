from typing import Any
from typing import Callable
from typing import Optional
from typing import TypeVar

import wrapt


F = TypeVar("F", bound=Callable[..., Any])


def iswrapped(obj: Any, attr: Optional[str] = None) -> bool:
    """Returns whether an attribute is wrapped or not."""
    if attr is not None:
        obj = getattr(obj, attr, None)
    return (hasattr(obj, "__wrapped__") and isinstance(obj, wrapt.ObjectProxy)) or hasattr(obj, "__dd_wrapped__")


def unwrap(obj: Any, attr: str) -> None:
    f = getattr(obj, attr)
    setattr(obj, attr, f.__wrapped__)
