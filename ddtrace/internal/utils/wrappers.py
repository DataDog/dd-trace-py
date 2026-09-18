from typing import Any
from typing import Callable
from typing import Optional
from typing import TypeVar

from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.wrapping import is_wrapped as _dd_is_wrapped


F = TypeVar("F", bound=Callable[..., Any])


def iswrapped(obj: Any, attr: Optional[str] = None) -> bool:
    """Returns whether an attribute is wrapped or not."""
    if attr is not None:
        obj = getattr(obj, attr, None)
    return (hasattr(obj, "__wrapped__") and is_wrapted(obj)) or _dd_is_wrapped(obj)


def unwrap(obj: Any, attr: str) -> None:
    f = getattr(obj, attr)
    setattr(obj, attr, f.__wrapped__)
