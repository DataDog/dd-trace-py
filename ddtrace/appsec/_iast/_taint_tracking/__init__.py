from io import BytesIO
from io import StringIO
import itertools
from typing import Any
from typing import Sequence
from typing import Tuple

from ddtrace.internal._unpatched import _threading as threading
from ddtrace.internal.logger import get_logger

from ..._constants import IAST
from ..._constants import IAST_SPAN_TAGS
from .._iast_request_context import is_iast_request_enabled
from .._metrics import _set_iast_error_metric
from .._metrics import _set_metric_iast_executed_source
from .._metrics import increment_iast_span_metric
from .._utils import _is_iast_debug_enabled
from .._utils import _is_iast_propagation_debug_enabled
from .._utils import _is_python_version_supported


log = get_logger(__name__)

if _is_python_version_supported():
    from ._native import OriginType
    from ._native import Source
    from ._native import TagMappingMode
    from ._native import get_ranges
    from ._native import origin_to_str
    from ._native import set_ranges
    from ._native import taint_range as TaintRange

    new_pyobject_id = ops.new_pyobject_id
    set_ranges_from_values = ops.set_ranges_from_values

__all__ = [
    "OriginType",
    "Source",
    "TagMappingMode",
    "TaintRange",
    "lower_aspect",
]


def iast_taint_log_error(msg):
    if _is_iast_debug_enabled():
        import inspect

        stack = inspect.stack()
        frame_info = "\n".join("%s %s" % (frame_info.filename, frame_info.lineno) for frame_info in stack[:7])
        log.debug("[IAST] Propagation error. %s:\n%s", msg, frame_info)
    _set_iast_error_metric("[IAST] Propagation error. %s" % msg)


def is_pyobject_tainted(pyobject: Any) -> bool:
    if not is_iast_request_enabled():
        return False
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):  # type: ignore[misc]
        return False

    try:
        return is_tainted(pyobject)
    except ValueError as e:
        iast_taint_log_error("Checking tainted object error: %s" % e)
    return False


def _taint_pyobject_base(pyobject: Any, source_name: Any, source_value: Any, source_origin=None) -> Any:
    if not is_iast_request_enabled():
        return pyobject

    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):  # type: ignore[misc]
        return pyobject
    # We need this validation in different condition if pyobject is not a text type and creates a side-effect such as
    # __len__ magic method call.
    pyobject_len = 0
    if isinstance(pyobject, IAST.TEXT_TYPES):
        pyobject_len = len(pyobject)
        if pyobject_len == 0:
            return pyobject

    if isinstance(source_name, (bytes, bytearray)):
        source_name = str(source_name, encoding="utf8", errors="ignore")
    if isinstance(source_name, OriginType):
        source_name = origin_to_str(source_name)

    if isinstance(source_value, (bytes, bytearray)):
        source_value = str(source_value, encoding="utf8", errors="ignore")
    if source_origin is None:
        source_origin = OriginType.PARAMETER

    try:
        pyobject_newid = set_ranges_from_values(pyobject, pyobject_len, source_name, source_value, source_origin)
        return pyobject_newid
    except ValueError as e:
        log.debug("Tainting object error (pyobject type %s): %s", type(pyobject), e, exc_info=True)
    return pyobject


def taint_pyobject(pyobject: Any, source_name: Any, source_value: Any, source_origin=None) -> Any:
    try:
        if source_origin is None:
            source_origin = OriginType.PARAMETER

        res = _taint_pyobject_base(pyobject, source_name, source_value, source_origin)
        _set_metric_iast_executed_source(source_origin)
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE, source_origin)
        return res
    except ValueError as e:
        log.debug("Tainting object error (pyobject type %s): %s", type(pyobject), e)
    return pyobject


def taint_pyobject_with_ranges(pyobject: Any, ranges: Tuple) -> bool:
    if not is_iast_request_enabled():
        return False
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):  # type: ignore[misc]
        return False
    try:
        set_ranges(pyobject, ranges)
        return True
    except ValueError as e:
        iast_taint_log_error("Tainting object with ranges error (pyobject type %s): %s" % (type(pyobject), e))
    return False


def get_tainted_ranges(pyobject: Any) -> Tuple:
    if not is_iast_request_enabled():
        return tuple()
    if not isinstance(pyobject, IAST.TAINTEABLE_TYPES):  # type: ignore[misc]
        return tuple()
    try:
        return get_ranges(pyobject)
    except ValueError as e:
        iast_taint_log_error("Get ranges error (pyobject type %s): %s" % (type(pyobject), e))
    return tuple()


if _is_iast_propagation_debug_enabled():
    TAINTED_FRAMES = []

    def trace_calls_and_returns(frame, event, arg):
        co = frame.f_code
        func_name = co.co_name
        if func_name == "write":
            # Ignore write() calls from print statements
            return
        if func_name in ("is_pyobject_tainted", "__repr__"):
            return
        line_no = frame.f_lineno
        filename = co.co_filename
        if "ddtrace" in filename:
            return
        if event == "call":
            f_locals = frame.f_locals
            try:
                if any([is_pyobject_tainted(f_locals[arg]) for arg in f_locals]):
                    TAINTED_FRAMES.append(frame)
                    log.debug("Call to %s on line %s of %s, args: %s", func_name, line_no, filename, frame.f_locals)
                    log.debug("Tainted arguments:")
                    for arg in f_locals:
                        if is_pyobject_tainted(f_locals[arg]):
                            log.debug("\t%s: %s", arg, f_locals[arg])
                    log.debug("-----")
                return trace_calls_and_returns
            except AttributeError:
                pass
        elif event == "return":
            if frame in TAINTED_FRAMES:
                TAINTED_FRAMES.remove(frame)
                log.debug("Return from %s on line %d of %s, return value: %s", func_name, line_no, filename, arg)
                if isinstance(arg, (str, bytes, bytearray, BytesIO, StringIO, list, tuple, dict)):
                    if (
                        (isinstance(arg, (str, bytes, bytearray, BytesIO, StringIO)) and is_pyobject_tainted(arg))
                        or (isinstance(arg, (list, tuple)) and any([is_pyobject_tainted(x) for x in arg]))
                        or (isinstance(arg, dict) and any([is_pyobject_tainted(x) for x in arg.values()]))
                    ):
                        log.debug("Return value is tainted")
                    else:
                        log.debug("Return value is NOT tainted")
                log.debug("-----")
        return

    threading.settrace(trace_calls_and_returns)


def copy_ranges_to_string(pyobject: str, ranges: Sequence[TaintRange]) -> str:
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


# Given a list of ranges, try to match them with the iterable and return a new iterable with a new range applied that
# matched the original one Source. If no range matches, take the Source from the first one.
def copy_ranges_to_iterable_with_strings(iterable: Sequence[str], ranges: Sequence[TaintRange]) -> Sequence[str]:
    iterable_type = type(iterable)

    new_result = []
    # do this so it doesn't consume a potential generator
    items, items_backup = itertools.tee(iterable)
    for i in items_backup:
        i = copy_ranges_to_string(i, ranges)
        new_result.append(i)

    return iterable_type(new_result)  # type: ignore[call-arg]
