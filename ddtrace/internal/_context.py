from typing import TYPE_CHECKING

from ddtrace.provider import _DD_CONTEXTVAR
from ddtrace.span import Span


if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Union

    from ddtrace.context import Context


def get_item(key, span=None):
    # type: (str, Optional[Span]) -> Optional[Any]
    """Get and item from the context of a trace."""
    ctx = span if span is not None else _DD_CONTEXTVAR.get()
    if not isinstance(ctx, Span) or ctx._local_root is None:
        raise ValueError("No context found")
    return ctx._local_root._get_ctx_item(key)


def get_items(keys, span=None):
    # type: (List[str], Optional[Span]) -> List[Optional[Any]]
    """Get multiple items from the context of a trace."""
    ctx = span if span is not None else _DD_CONTEXTVAR.get()  # type: Optional[Union[Context, Span]]
    if not isinstance(ctx, Span) or ctx._local_root is None:
        raise ValueError("No context found")
    return [ctx._local_root._get_ctx_item(k) for k in keys]


def set_item(key, val, span=None):
    # type: (str, Any, Optional[Span]) -> None
    """Set an item in the context of a trace."""
    ctx = span if span is not None else _DD_CONTEXTVAR.get()  # type: Optional[Union[Context, Span]]
    if not isinstance(ctx, Span) or ctx._local_root is None:
        raise ValueError("No context found")
    ctx._local_root._set_ctx_item(key, val)


def set_items(kvs, span=None):
    # type: (Dict[str, Any], Optional[Span]) -> None
    """Set multiple items in the context of a trace."""
    ctx = span if span is not None else _DD_CONTEXTVAR.get()  # type: Optional[Union[Context, Span]]
    if not isinstance(ctx, Span) or ctx._local_root is None:
        raise ValueError("No context found")
    ctx._local_root._set_ctx_items(kvs)
