from typing import TYPE_CHECKING

from .provider import _DD_CONTEXTVAR
from .span import Span


if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Union

    from .context import Context


def get(key, span=None):
    # type: (str, Optional[Span]) -> Optional[Any]
    ctx = span if span is not None else _DD_CONTEXTVAR.get()
    if not isinstance(ctx, Span) or ctx._local_root is None:
        raise ValueError("No context found")
    return ctx._local_root._get_ctx_item(key)


def get_multi(keys, span=None):
    # type: (List[str], Optional[Span]) -> List[Optional[Any]]
    ctx = span if span is not None else _DD_CONTEXTVAR.get()  # type: Optional[Union[Context, Span]]
    if not isinstance(ctx, Span) or ctx._local_root is None:
        raise ValueError("No context found")
    return [ctx._local_root._get_ctx_item(k) for k in keys]


def set_item(key, val, span=None):
    # type: (str, Any, Optional[Span]) -> None
    ctx = span if span is not None else _DD_CONTEXTVAR.get()  # type: Optional[Union[Context, Span]]
    if not isinstance(ctx, Span) or ctx._local_root is None:
        raise ValueError("No context found")
    ctx._local_root._set_ctx_item(key, val)


def set_multi(kvs, span=None):
    # type: (Dict[str, Any], Optional[Span]) -> None
    ctx = span if span is not None else _DD_CONTEXTVAR.get()  # type: Optional[Union[Context, Span]]
    if not isinstance(ctx, Span) or ctx._local_root is None:
        raise ValueError("No context found")
    for k, v in kvs.items():
        ctx._local_root._set_ctx_item(k, v)
