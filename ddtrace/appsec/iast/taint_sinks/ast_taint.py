from typing import TYPE_CHECKING

from ..constants import DEFAULT_WEAK_RANDOMNESS_FUNCTIONS
from .weak_randomness import WeakRandomness


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable


def ast_funcion(
    func,  # type: Callable
    *args,  # type: Any
    **kwargs  # type: Any
):  # type: (...) -> Any

    cls = getattr(func, "__self__", None)
    func_name = getattr(func, "__name__", None)
    if cls and func_name:
        try:
            cls_name = cls.__class__.__name__
        except AttributeError:
            cls_name = None
    else:
        cls_name = None
    wrapped = None

    if cls_name == "Random" and func_name in DEFAULT_WEAK_RANDOMNESS_FUNCTIONS:
        # Weak, run the analyzer
        WeakRandomness.report(evidence_value=cls_name + "." + func_name)
    if wrapped:
        func = wrapped

    return func(*args, **kwargs)
