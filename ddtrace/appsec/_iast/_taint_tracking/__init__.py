from ddtrace.appsec._iast._taint_tracking._native import ops  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_format import _format_aspect  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_helpers import _convert_escaped_text_to_tainted_text

# noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_helpers import as_formatted_evidence  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_helpers import common_replace  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_helpers import parse_params  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_helpers import set_ranges_on_splitted  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_split import _aspect_rsplit  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_split import _aspect_split  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_split import _aspect_splitlines  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspects_ospath import _aspect_ospathbasename  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspects_ospath import _aspect_ospathdirname  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspects_ospath import _aspect_ospathjoin  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspects_ospath import _aspect_ospathnormcase  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspects_ospath import _aspect_ospathsplit  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspects_ospath import _aspect_ospathsplitdrive  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspects_ospath import _aspect_ospathsplitext  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspects_ospath import _aspect_ospathsplitroot  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.initializer import active_map_addreses_size  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.initializer import debug_taint_map  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.initializer import initializer_size  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.initializer import num_objects_tainted  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import OriginType  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import Source  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import TagMappingMode  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import are_all_text_all_ranges  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import copy_and_shift_ranges_from_strings  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import copy_ranges_from_strings  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import get_range_by_hash  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import get_ranges  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import is_tainted  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import origin_to_str  # noqa: F401

# noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import set_ranges  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import shift_taint_range  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import shift_taint_ranges  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import str_to_origin  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import taint_range as TaintRange  # noqa: F401
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

__all__ = [
    "OriginType",
    "Source",
    "TagMappingMode",
    "TaintRange",
    "_aspect_modulo",
    "_aspect_ospathbasename",
    "_aspect_ospathdirname",
    "_aspect_ospathjoin",
    "_aspect_ospathnormcase",
    "_aspect_ospathsplit",
    "_aspect_ospathsplitdrive",
    "_aspect_ospathsplitext",
    "_aspect_ospathsplitroot",
    "_aspect_rsplit",
    "_aspect_split",
    "_aspect_splitlines",
    "_aspect_str",
    "_convert_escaped_text_to_tainted_text",
    "_format_aspect",
    "active_map_addreses_size",
    "are_all_text_all_ranges",
    "as_formatted_evidence",
    "aspect_helpers",
    "common_replace",
    "copy_and_shift_ranges_from_strings",
    "copy_ranges_from_strings",
    "create_context",
    "debug_taint_map",
    "get_range_by_hash",
    "get_ranges",
    "initializer_size",
    "is_tainted",
    "is_notinterned_notfasttainted_unicode",
    "modulo_aspect",
    "new_pyobject_id",
    "num_objects_tainted",
    "origin_to_str",
    "parse_params",
    "reset_context",
    "reset_contexts",
    "set_fast_tainted_if_notinterned_unicode",
    "set_ranges",
    "set_ranges_on_splitted",
    "setup",
    "shift_taint_range",
    "shift_taint_ranges",
    "str_to_origin",
    "taint_pyobject",
]


def iast_taint_log_error(msg):
    if _is_iast_debug_enabled():
        import inspect

        stack = inspect.stack()
        frame_info = "\n".join("%s %s" % (frame_info.filename, frame_info.lineno) for frame_info in stack[:15])
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


def copy_ranges_to_string(pyobject, ranges):
    # type: (str, Sequence[TaintRange]) -> str
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


# Given a list of ranges, try to match them with the iterable and return a new iterable with a new range applied that
# matched the original one Source. If no range matches, take the Source from the first one.
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
