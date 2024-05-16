import os
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool

from ..._constants import IAST
from .._metrics import _set_iast_error_metric
from .._metrics import _set_metric_iast_executed_source
from .._utils import _is_python_version_supported


log = get_logger(__name__)

if _is_python_version_supported():
    from ._native import ops
    from ._native.aspect_format import _format_aspect
    from ._native.aspect_helpers import _convert_escaped_text_to_tainted_text
    from ._native.aspect_helpers import as_formatted_evidence
    from ._native.aspect_helpers import common_replace
    from ._native.aspect_helpers import parse_params
    from ._native.aspect_helpers import set_ranges_on_splitted
    from ._native.aspect_split import _aspect_rsplit
    from ._native.aspect_split import _aspect_split
    from ._native.aspect_split import _aspect_splitlines
    from ._native.aspects_ospath import _aspect_ospathbasename
    from ._native.aspects_ospath import _aspect_ospathdirname
    from ._native.aspects_ospath import _aspect_ospathjoin
    from ._native.aspects_ospath import _aspect_ospathnormcase
    from ._native.aspects_ospath import _aspect_ospathsplit
    from ._native.aspects_ospath import _aspect_ospathsplitdrive
    from ._native.aspects_ospath import _aspect_ospathsplitext
    from ._native.aspects_ospath import _aspect_ospathsplitroot
    from ._native.initializer import active_map_addreses_size
    from ._native.initializer import create_context
    from ._native.initializer import debug_taint_map
    from ._native.initializer import initializer_size
    from ._native.initializer import num_objects_tainted
    from ._native.initializer import reset_context
    from ._native.taint_tracking import OriginType
    from ._native.taint_tracking import Source
    from ._native.taint_tracking import TagMappingMode
    from ._native.taint_tracking import are_all_text_all_ranges
    from ._native.taint_tracking import copy_and_shift_ranges_from_strings
    from ._native.taint_tracking import copy_ranges_from_strings
    from ._native.taint_tracking import get_range_by_hash
    from ._native.taint_tracking import get_ranges
    from ._native.taint_tracking import is_notinterned_notfasttainted_unicode
    from ._native.taint_tracking import is_tainted
    from ._native.taint_tracking import origin_to_str
    from ._native.taint_tracking import set_fast_tainted_if_notinterned_unicode
    from ._native.taint_tracking import set_ranges
    from ._native.taint_tracking import shift_taint_range
    from ._native.taint_tracking import shift_taint_ranges
    from ._native.taint_tracking import str_to_origin
    from ._native.taint_tracking import taint_range as TaintRange

    new_pyobject_id = ops.new_pyobject_id
    set_ranges_from_values = ops.set_ranges_from_values


__all__ = [
    "_convert_escaped_text_to_tainted_text",
    "new_pyobject_id",
    "setup",
    "Source",
    "OriginType",
    "TagMappingMode",
    "TaintRange",
    "get_ranges",
    "set_ranges",
    "copy_ranges_from_strings",
    "copy_and_shift_ranges_from_strings",
    "are_all_text_all_ranges",
    "shift_taint_range",
    "shift_taint_ranges",
    "get_range_by_hash",
    "is_notinterned_notfasttainted_unicode",
    "set_fast_tainted_if_notinterned_unicode",
    "aspect_helpers",
    "reset_context",
    "initializer_size",
    "active_map_addreses_size",
    "create_context",
    "str_to_origin",
    "origin_to_str",
    "common_replace",
    "_aspect_ospathjoin",
    "_aspect_split",
    "_aspect_rsplit",
    "_aspect_splitlines",
    "_aspect_ospathbasename",
    "_aspect_ospathdirname",
    "_aspect_ospathnormcase",
    "_aspect_ospathsplit",
    "_aspect_ospathsplitext",
    "_aspect_ospathsplitdrive",
    "_aspect_ospathsplitroot",
    "_format_aspect",
    "as_formatted_evidence",
    "parse_params",
    "set_ranges_on_splitted",
    "num_objects_tainted",
    "debug_taint_map",
    "iast_taint_log_error",
]


def iast_taint_log_error(msg):
    if asbool(os.environ.get(IAST.ENV_DEBUG, "false")):
        import inspect

        stack = inspect.stack()
        frame_info = "\n".join("%s %s" % (frame_info.filename, frame_info.lineno) for frame_info in stack[:7])
        log.debug("%s:\n%s", msg, frame_info)
        _set_iast_error_metric("IAST propagation error. %s" % msg)


def is_pyobject_tainted(pyobject: Any) -> bool:
    if not isinstance(pyobject, IAST.TEXT_TYPES):
        return False

    try:
        return is_tainted(pyobject)
    except ValueError as e:
        iast_taint_log_error("Checking tainted object error: %s" % e)
    return False


def taint_pyobject(pyobject: Any, source_name: Any, source_value: Any, source_origin=None) -> Any:
    # Pyobject must be Text with len > 1
    if not isinstance(pyobject, IAST.TEXT_TYPES):
        return pyobject
    # We need this validation in different contition if pyobject is not a text type and creates a side-effect such as
    # __len__ magic method call.
    if len(pyobject) == 0:
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
        pyobject_newid = set_ranges_from_values(pyobject, len(pyobject), source_name, source_value, source_origin)
        _set_metric_iast_executed_source(source_origin)
        return pyobject_newid
    except ValueError as e:
        iast_taint_log_error("Tainting object error (pyobject type %s): %s" % (type(pyobject), e))
    return pyobject


def taint_pyobject_with_ranges(pyobject: Any, ranges: Tuple) -> bool:
    if not isinstance(pyobject, IAST.TEXT_TYPES):
        return False
    try:
        set_ranges(pyobject, ranges)
        return True
    except ValueError as e:
        iast_taint_log_error("Tainting object with ranges error (pyobject type %s): %s" % (type(pyobject), e))
    return False


def get_tainted_ranges(pyobject: Any) -> Tuple:
    if not isinstance(pyobject, IAST.TEXT_TYPES):
        return tuple()
    try:
        return get_ranges(pyobject)
    except ValueError as e:
        iast_taint_log_error("Get ranges error (pyobject type %s): %s" % (type(pyobject), e))
    return tuple()


def taint_ranges_as_evidence_info(pyobject: Any) -> Tuple[List[Dict[str, Union[Any, int]]], List[Source]]:
    # TODO: This function is deprecated.
    #  Redaction migrated to `ddtrace.appsec._iast._evidence_redaction._sensitive_handler` but we need to migrate
    #  all vulnerabilities to use it first.
    value_parts = []
    sources = list()
    current_pos = 0
    tainted_ranges = get_tainted_ranges(pyobject)
    if not len(tainted_ranges):
        return ([{"value": pyobject}], list())

    for _range in tainted_ranges:
        if _range.start > current_pos:
            value_parts.append({"value": pyobject[current_pos : _range.start]})

        if _range.source not in sources:
            sources.append(_range.source)

        value_parts.append(
            {
                "value": pyobject[_range.start : _range.start + _range.length],
                "source": sources.index(_range.source),
            }
        )
        current_pos = _range.start + _range.length

    if current_pos < len(pyobject):
        value_parts.append({"value": pyobject[current_pos:]})

    return value_parts, sources
