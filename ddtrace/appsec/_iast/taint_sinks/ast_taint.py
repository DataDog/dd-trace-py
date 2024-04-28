from typing import TYPE_CHECKING  # noqa:F401

from ..._constants import IAST_SPAN_TAGS
from .._metrics import _set_metric_iast_executed_sink
from .._metrics import increment_iast_span_metric
from ..constants import DEFAULT_PATH_TRAVERSAL_FUNCTIONS
from ..constants import DEFAULT_WEAK_RANDOMNESS_FUNCTIONS
from .path_traversal import check_and_report_path_traversal
from .weak_randomness import WeakRandomness


if TYPE_CHECKING:
    from typing import Any  # noqa:F401
    from typing import Callable  # noqa:F401


# TODO: we also need a native version of this function!
def ast_function(
    func,  # type: Callable
    flag_added_args,  # type: Any
    *args,  # type: Any
    **kwargs,  # type: Any
):  # type: (...) -> Any
    instance = getattr(func, "__self__", None)
    func_name = getattr(func, "__name__", None)
    cls_name = ""
    if instance is not None and func_name:
        try:
            cls_name = instance.__class__.__name__
        except AttributeError:
            pass

    if flag_added_args > 0:
        args = args[flag_added_args:]

    if (
        instance.__class__.__module__ == "random"
        and cls_name == "Random"
        and func_name in DEFAULT_WEAK_RANDOMNESS_FUNCTIONS
    ):
        # Weak, run the analyzer
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, WeakRandomness.vulnerability_type)
        _set_metric_iast_executed_sink(WeakRandomness.vulnerability_type)
        WeakRandomness.report(evidence_value=cls_name + "." + func_name)
    elif hasattr(func, "__module__") and DEFAULT_PATH_TRAVERSAL_FUNCTIONS.get(func.__module__):
        if func_name in DEFAULT_PATH_TRAVERSAL_FUNCTIONS[func.__module__]:
            check_and_report_path_traversal(*args, **kwargs)
    return func(*args, **kwargs)
