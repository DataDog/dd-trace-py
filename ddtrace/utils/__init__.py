from typing import Any
from typing import Dict
from typing import List
from typing import Optional


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
            raise ArgumentError("%s (at position %d)" % (kw, pos))
