# #!/usr/bin/env python3
# flake8: noqa
from typing import TYPE_CHECKING

from .._metrics import _set_metric_iast_executed_source
from .._utils import _is_python_version_supported


if _is_python_version_supported():
    from .. import oce
    from ._native import ops
    from ._native.aspect_helpers import _convert_escaped_text_to_tainted_text
    from ._native.aspect_helpers import as_formatted_evidence
    from ._native.aspect_helpers import common_replace
    from ._native.aspect_helpers import parse_params
    from ._native.initializer import active_map_addreses_size
    from ._native.initializer import create_context
    from ._native.initializer import destroy_context
    from ._native.initializer import initializer_size
    from ._native.initializer import num_objects_tainted
    from ._native.initializer import reset_context
    from ._native.taint_tracking import OriginType
    from ._native.taint_tracking import Source
    from ._native.taint_tracking import TagMappingMode
    from ._native.taint_tracking import are_all_text_all_ranges
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
    is_pyobject_tainted = is_tainted

if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Tuple
    from typing import Union


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
    "are_all_text_all_ranges",
    "shift_taint_range",
    "shift_taint_ranges",
    "get_range_by_hash",
    "is_notinterned_notfasttainted_unicode",
    "set_fast_tainted_if_notinterned_unicode",
    "aspect_helpers",
    "reset_context",
    "destroy_context",
    "initializer_size",
    "active_map_addreses_size",
    "create_context",
    "str_to_origin",
    "origin_to_str",
    "common_replace",
    "as_formatted_evidence",
    "parse_params",
    "num_objects_tainted",
]


def taint_pyobject(pyobject, source_name, source_value, source_origin=None, start=0, len_pyobject=None):
    # type: (Any, Any, Any, OriginType, int, Optional[int]) -> Any
    # Request is not analyzed
    if not oce.request_has_quota:
        return pyobject
    # Pyobject must be Text with len > 1
    if not pyobject or not isinstance(pyobject, (str, bytes, bytearray)):
        return pyobject

    if not len_pyobject:
        len_pyobject = len(pyobject)
    pyobject_newid = new_pyobject_id(pyobject, len_pyobject)
    if isinstance(source_name, (bytes, bytearray)):
        source_name = str(source_name, encoding="utf8")
    if isinstance(source_value, (bytes, bytearray)):
        source_value = str(source_value, encoding="utf8")
    if source_origin is None:
        source_origin = OriginType.PARAMETER
    source = Source(source_name, source_value, source_origin)
    pyobject_range = TaintRange(start, len_pyobject, source)
    set_ranges(pyobject_newid, [pyobject_range])
    _set_metric_iast_executed_source(source_origin)
    return pyobject_newid


def taint_pyobject_with_ranges(pyobject, ranges):  # type: (Any, tuple) -> None
    set_ranges(pyobject, tuple(ranges))


def get_tainted_ranges(pyobject):  # type: (Any) -> tuple
    return get_ranges(pyobject)


def taint_ranges_as_evidence_info(pyobject):
    # type: (Any) -> Tuple[List[Dict[str, Union[Any, int]]], list[Source]]
    value_parts = []
    sources = []
    current_pos = 0
    tainted_ranges = get_tainted_ranges(pyobject)
    if not len(tainted_ranges):
        return ([{"value": pyobject}], [])

    for _range in tainted_ranges:
        if _range.start > current_pos:
            value_parts.append({"value": pyobject[current_pos : _range.start]})

        if _range.source not in sources:
            sources.append(_range.source)

        value_parts.append(
            {"value": pyobject[_range.start : _range.start + _range.length], "source": sources.index(_range.source)}
        )
        current_pos = _range.start + _range.length

    if current_pos < len(pyobject):
        value_parts.append({"value": pyobject[current_pos:]})

    return value_parts, sources
