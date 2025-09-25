from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import Optional  # noqa:F401
from typing import TypeVar  # noqa:F401

import wrapt


F = TypeVar("F", bound=Callable[..., Any])


class NotWrappedError(Exception):
    pass


def iswrapped(obj, attr=None):
    # type: (Any, Optional[str]) -> bool
    """Returns whether an attribute is wrapped or not."""
    if attr is not None:
        obj = getattr(obj, attr, None)
    return (hasattr(obj, "__wrapped__") and isinstance(obj, wrapt.ObjectProxy)) or hasattr(obj, "__dd_wrapped__")


def unwrap(obj: Any, attr: str) -> None:
    try:
        setattr(obj, attr, getattr(obj, attr).__wrapped__)
    except AttributeError:
        raise NotWrappedError("{}.{} is not wrapped".format(obj, attr))
