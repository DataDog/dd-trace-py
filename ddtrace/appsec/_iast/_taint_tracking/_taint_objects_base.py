from typing import Any
from typing import Tuple

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._logs import iast_propagation_debug_log
from ddtrace.appsec._iast._logs import iast_propagation_error_log
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._context import is_in_taint_map


def _taint_pyobject_base(pyobject: Any, source_name: Any, source_value: Any, source_origin=None, contextid=None) -> Any:
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
        - Only applies to taintable types defined in IAST.TAINTEABLE_TYPES
        - Returns unmodified object for empty strings
        - Automatically handles bytes/bytearray to str conversion
    """
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES) or not pyobject:
        return pyobject

    if isinstance(source_name, (bytes, bytearray)):
        source_name = source_name.decode("utf-8", errors="ignore")
    elif isinstance(source_name, OriginType):
        source_name = origin_to_str(source_name)

    if isinstance(source_value, (bytes, bytearray)):
        source_value = source_value.decode("utf-8", errors="ignore")

    if source_origin is None:
        source_origin = OriginType.PARAMETER

    try:
        pyobject_len = len(pyobject) if isinstance(pyobject, IAST.TEXT_TYPES) else 0
        return taint_pyobject(pyobject, pyobject_len, source_name, source_value, source_origin, contextid)
    except ValueError:
        iast_propagation_debug_log(f"Tainting object error (pyobject type {type(pyobject)})", exc_info=True)
        return pyobject


def get_tainted_ranges(pyobject: Any) -> Tuple:
    if not is_iast_request_enabled():
        return tuple()
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):
        return tuple()
    try:
        return get_ranges(pyobject)
    except ValueError as e:
        iast_propagation_error_log(f"get_tainted_ranges error (pyobject type {type(pyobject)})", exc=e)
    return tuple()


def is_pyobject_tainted(pyobject: Any) -> bool:
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):
        return False

    try:
        return is_in_taint_map(pyobject)
    except ValueError as e:
        iast_propagation_error_log("Checking tainted object error", exc=e)
    return False
