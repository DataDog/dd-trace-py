import itertools
from typing import Any
from typing import Sequence
from typing import Tuple

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._logs import iast_propagation_debug_log
from ddtrace.appsec._iast._logs import iast_propagation_error_log
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_source
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking import is_tainted
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking import set_ranges_from_values
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def is_pyobject_tainted(pyobject: Any) -> bool:
    if not asm_config.is_iast_request_enabled:
        return False
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):  # type: ignore[misc]
        return False

    try:
        return is_tainted(pyobject)
    except ValueError as e:
        iast_propagation_error_log(f"Checking tainted object error: {e}")
    return False


def _taint_pyobject_base(pyobject: Any, source_name: Any, source_value: Any, source_origin=None) -> Any:
    """Mark a Python object as tainted with information about its origin.

    This function is the base for marking objects as tainted, setting their origin and range.
    It is optimized for:
    1. Early validations to avoid unnecessary operations
    2. Efficient type conversions
    3. Special case handling (empty objects)
    4. Robust error handling

    Performance optimizations:
    - Early return for disabled IAST or non-taintable types
    - Efficient string length calculation only when needed
    - Optimized bytes/bytearray to string conversion using decode()
    - Minimized object allocations and method calls

    Args:
        pyobject (Any): The object to mark as tainted. Must be a taintable type.
        source_name (Any): Name of the taint source (e.g., parameter name).
        source_value (Any): Original value that caused the taint.
        source_origin (Optional[OriginType]): Origin of the taint. Defaults to PARAMETER.

    Returns:
        Any: The tainted object if operation was successful, original object if failed.

    Note:
        - Only works if IAST is enabled (asm_config.is_iast_request_enabled)
        - Only applies to taintable types defined in IAST.TAINTEABLE_TYPES
        - Returns unmodified object for empty strings
        - Automatically handles bytes/bytearray to str conversion
    """
    # Early return if IAST is disabled
    if not asm_config.is_iast_request_enabled:
        return pyobject

    # Early type validation
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):  # type: ignore[misc]
        return pyobject

    # Fast path for empty strings
    if isinstance(pyobject, IAST.TEXT_TYPES) and not pyobject:
        return pyobject

    # Efficient source_name conversion
    if isinstance(source_name, (bytes, bytearray)):
        source_name = source_name.decode("utf-8", errors="ignore")
    elif isinstance(source_name, OriginType):
        source_name = origin_to_str(source_name)

    # Efficient source_value conversion
    if isinstance(source_value, (bytes, bytearray)):
        source_value = source_value.decode("utf-8", errors="ignore")

    # Default source_origin
    if source_origin is None:
        source_origin = OriginType.PARAMETER

    try:
        # Calculate length only for text types
        pyobject_len = len(pyobject) if isinstance(pyobject, IAST.TEXT_TYPES) else 0
        return set_ranges_from_values(pyobject, pyobject_len, source_name, source_value, source_origin)
    except ValueError:
        iast_propagation_debug_log(f"Tainting object error (pyobject type {type(pyobject)})", exc_info=True)
        return pyobject


def taint_pyobject_with_ranges(pyobject: Any, ranges: Tuple) -> bool:
    if not asm_config.is_iast_request_enabled:
        return False
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):  # type: ignore[misc]
        return False
    try:
        set_ranges(pyobject, ranges)
        return True
    except ValueError as e:
        iast_propagation_error_log(f"taint_pyobject_with_ranges error (pyobject type {type(pyobject)}): {e}")
    return False


def get_tainted_ranges(pyobject: Any) -> Tuple:
    if not asm_config.is_iast_request_enabled:
        return tuple()
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):  # type: ignore[misc]
        return tuple()
    try:
        return get_ranges(pyobject)
    except ValueError as e:
        iast_propagation_error_log(f"get_tainted_ranges error (pyobject type {type(pyobject)}): {e}")
    return tuple()


def taint_pyobject(pyobject: Any, source_name: Any, source_value: Any, source_origin=None) -> Any:
    try:
        if source_origin is None:
            source_origin = OriginType.PARAMETER

        res = _taint_pyobject_base(pyobject, source_name, source_value, source_origin)
        _set_metric_iast_executed_source(source_origin)
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE, source_origin)
        return res
    except ValueError:
        iast_propagation_debug_log(f"taint_pyobject error (pyobject type {type(pyobject)})", exc_info=True)
    return pyobject


def copy_ranges_to_string(pyobject: str, ranges: Sequence[TaintRange]) -> str:
    # NB this function uses comment-based type annotation because TaintRange is conditionally imported
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):  # type: ignore[misc]
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
            )
            break
    else:
        # no total match found, maybe partial match, just take the first one
        pyobject = _taint_pyobject_base(
            pyobject=pyobject,
            source_name=ranges[0].source.name,
            source_value=ranges[0].source.value,
            source_origin=ranges[0].source.origin,
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
