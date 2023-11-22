from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import six


class ArgumentError(Exception):
    """
    This is raised when an argument lookup, either by position or by keyword, is
    not found.
    """


def get_argument_value(
    args,  # type: List[Any]
    kwargs,  # type: Dict[str, Any]
    pos,  # type: int
    kw,  # type: str
    optional=False,  # type: bool
):
    # type: (...) -> Optional[Any]
    """
    This function parses the value of a target function argument that may have been
    passed in as a positional argument or a keyword argument. Because monkey-patched
    functions do not define the same signature as their target function, the value of
    arguments must be inferred from the packed args and kwargs.
    Keyword arguments are prioritized, followed by the positional argument. If the
    argument cannot be resolved, an ``ArgumentError`` exception is raised, which could
    be used, e.g., to handle a default value by the caller.
    :param args: Positional arguments
    :param kwargs: Keyword arguments
    :param pos: The positional index of the argument if passed in as a positional arg
    :param kw: The name of the keyword if passed in as a keyword argument
    :return: The value of the target argument
    """
    try:
        return kwargs[kw]
    except KeyError:
        try:
            return args[pos]
        except IndexError:
            if optional:
                return None
            raise ArgumentError("%s (at position %d)" % (kw, pos))


def set_argument_value(
    args,  # type: Tuple[Any, ...]
    kwargs,  # type: Dict[str, Any]
    pos,  # type: int
    kw,  # type: str
    value,  # type: Any
):
    # type: (...) -> Tuple[Tuple[Any, ...], Dict[str, Any]]
    """
    Returns a new args, kwargs with the given value updated
    :param args: Positional arguments
    :param kwargs: Keyword arguments
    :param pos: The positional index of the argument
    :param kw: The name of the keyword
    :param value: The new value of the target argument
    :return: Updated args and kwargs
    """
    if len(args) > pos:
        args = args[:pos] + (value,) + args[pos + 1 :]
    elif kw in kwargs:
        kwargs[kw] = value
    else:
        raise ArgumentError("%s (at position %d) is invalid" % (kw, pos))

    return args, kwargs


def _get_metas_to_propagate(context):
    # type: (Any) -> List[Tuple[str, str]]
    metas_to_propagate = []
    for k, v in context._meta.items():
        if isinstance(k, six.string_types) and k.startswith("_dd.p."):
            metas_to_propagate.append((k, v))
    return metas_to_propagate
