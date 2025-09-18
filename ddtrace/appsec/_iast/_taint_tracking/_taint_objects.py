import itertools
from typing import Any
from typing import Sequence
from typing import Tuple

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_request_context_base import _get_iast_context_id
from ddtrace.appsec._iast._logs import iast_propagation_debug_log
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_source
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import _taint_pyobject_base
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def taint_pyobject(pyobject: Any, source_name: Any, source_value: Any, source_origin=None) -> Any:
    try:
        if (contextid := _get_iast_context_id()) is not None:
            if source_origin is None:
                source_origin = OriginType.PARAMETER
            res = _taint_pyobject_base(pyobject, source_name, source_value, source_origin, contextid)
            _set_metric_iast_executed_source(source_origin)
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE, source_origin)
            return res
    except ValueError:
        iast_propagation_debug_log(f"taint_pyobject error (pyobject type {type(pyobject)})", exc_info=True)
    return pyobject


def copy_ranges_to_string(pyobject: str, ranges: Sequence[TaintRange]) -> str:
    # NB this function uses comment-based type annotation because TaintRange is conditionally imported
    if (contextid := _get_iast_context_id()) is not None:
        if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):
            return pyobject

        for r in ranges:
            _is_string_in_source_value = False
            if r.source.value:
                if isinstance(pyobject, (bytes, bytearray)):
                    pyobject_str = str(pyobject, encoding="utf8", errors="ignore")
                else:
                    pyobject_str = pyobject
                _is_string_in_source_value = pyobject_str in r.source.value

            if _is_string_in_source_value:
                pyobject = _taint_pyobject_base(
                    pyobject=pyobject,
                    source_name=r.source.name,
                    source_value=r.source.value,
                    source_origin=r.source.origin,
                    contextid=contextid,
                )
                break
        else:
            # no total match found, maybe partial match, just take the first one
            pyobject = _taint_pyobject_base(
                pyobject=pyobject,
                source_name=ranges[0].source.name,
                source_value=ranges[0].source.value,
                source_origin=ranges[0].source.origin,
                contextid=contextid,
            )
    return pyobject


def copy_ranges_to_iterable_with_strings(iterable, ranges):
    # type: (Sequence[str], Sequence[TaintRange]) -> Sequence[str]
    # NB this function uses comment-based type annotation because TaintRange is conditionally imported
    iterable_type = type(iterable)

    new_result = []
    # do this so it doesn't consume a potential generator
    items, items_backup = itertools.tee(iterable)
    for i in items_backup:
        i = copy_ranges_to_string(i, ranges)
        new_result.append(i)

    return iterable_type(new_result)  # type: ignore[call-arg]


def taint_pyobject_with_ranges(pyobject: Any, ranges: Tuple) -> bool:
    if (contextid := _get_iast_context_id()) is None:
        return False
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):
        return False
    return set_ranges(pyobject, ranges, contextid)
