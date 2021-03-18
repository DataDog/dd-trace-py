from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..vendor import debtcollector


# https://stackoverflow.com/a/26853961
def merge_dicts(x, y):
    """Returns a copy of y merged into x."""
    z = x.copy()  # start with x's keys and values
    z.update(y)  # modifies z with y's keys and values & returns None
    return z


def get_module_name(module):
    """Returns a module's name or None if one cannot be found.
    Relevant PEP: https://www.python.org/dev/peps/pep-0451/
    """
    if hasattr(module, "__spec__"):
        return module.__spec__.name
    return getattr(module, "__name__", None)


# Based on: https://stackoverflow.com/a/7864317
class removed_classproperty(property):
    def __get__(self, cls, owner):
        debtcollector.deprecate(
            "Usage of ddtrace.ext.AppTypes is not longer supported, please use ddtrace.ext.SpanTypes"
        )
        return classmethod(self.fget).__get__(None, owner)()


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
