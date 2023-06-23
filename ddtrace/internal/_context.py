from typing import TYPE_CHECKING

from ddtrace.internal import core
from ddtrace.provider import _DD_CONTEXTVAR
from ddtrace.span import Span


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Union

    from ddtrace.context import Context


def get_item(key, span=None):
    # type: (str, Optional[Span]) -> Optional[Any]
    return core.get_item(key, span=span)


def get_items(keys, span=None):
    # type: (List[str], Optional[Span]) -> List[Optional[Any]]
    """Get multiple items from the context of a trace."""
    return core.get_items(keys, span=span)


def set_item(key, val, span=None):
    # type: (str, Any, Optional[Span]) -> None
    """Set an item in the context of a trace."""
    return core.set_item(key, val, span=span)


def set_items(kvs, span=None):
    # type: (Dict[str, Any], Optional[Span]) -> None
    """Set multiple items in the context of a trace."""
    return core.set_items(kvs, span=span)
