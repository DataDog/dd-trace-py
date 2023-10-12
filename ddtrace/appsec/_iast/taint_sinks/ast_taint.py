from typing import TYPE_CHECKING

from .._metrics import _set_metric_iast_executed_sink
from ..constants import DEFAULT_PATH_TRAVERSAL_FUNCTIONS
from ..constants import DEFAULT_WEAK_RANDOMNESS_FUNCTIONS
from .path_traversal import check_and_report_path_traversal
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
    cls_name = ""
    if cls and func_name:
        try:
            cls_name = cls.__class__.__name__
        except AttributeError:
            pass

    if cls.__class__.__module__ == "random" and cls_name == "Random" and func_name in DEFAULT_WEAK_RANDOMNESS_FUNCTIONS:
        # Weak, run the analyzer
        _set_metric_iast_executed_sink(WeakRandomness.vulnerability_type)
        WeakRandomness.report(evidence_value=cls_name + "." + func_name)
    elif hasattr(func, "__module__") and DEFAULT_PATH_TRAVERSAL_FUNCTIONS.get(func.__module__):
        if func_name in DEFAULT_PATH_TRAVERSAL_FUNCTIONS[func.__module__]:
            check_and_report_path_traversal(*args, **kwargs)
    return func(*args, **kwargs)
