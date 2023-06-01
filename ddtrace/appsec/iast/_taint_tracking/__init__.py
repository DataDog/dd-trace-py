# #!/usr/bin/env python3

from typing import TYPE_CHECKING

from ddtrace.appsec.iast import oce

if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Tuple
    from typing import Union
    from typing import Optional

from ddtrace.appsec.iast._taint_tracking._native import aspect_helpers  # noqa: F401
from ddtrace.appsec.iast._taint_tracking._native import ops  # noqa: F401
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import OriginType  # noqa: F401
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import Source
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import TaintRange
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import are_all_text_all_ranges
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import get_range_by_hash
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import get_ranges
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import is_notinterned_notfasttainted_unicode
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import origin_to_str  # noqa: F401
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import set_fast_tainted_if_notinterned_unicode
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import set_ranges
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import shift_taint_range
from ddtrace.appsec.iast._taint_tracking._native.taint_tracking import shift_taint_ranges


setup = ops.setup
new_pyobject_id = ops.new_pyobject_id
is_pyobject_tainted = ops.is_tainted

__all__ = [
    "new_pyobject_id",
    "setup",
    "Source",
    "OriginType",
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
]


def taint_pyobject(pyobject, source_name=None, source_value=None, source_origin=None, start=0, len_pyobject=None):
    # type: (Any, str, str, OriginType, int, Optional[int]) -> Any
    # Request is not analyzed
    if not oce.request_has_quota:
        return pyobject
    # Pyobject must be Text with len > 1
    if not pyobject or not isinstance(pyobject, (str, bytes, bytearray)):
        return pyobject

    if len_pyobject is None:
        len_pyobject = len(pyobject)
    pyobject = new_pyobject_id(pyobject, len_pyobject)
    source = Source(source_name, source_value, source_origin)
    pyobject_range = TaintRange(start, len_pyobject, source)
    set_ranges(pyobject, [pyobject_range])
    return pyobject


def taint_pyobject_with_ranges(pyobject, ranges):  # type: (Any, tuple) -> None
    set_ranges(pyobject, ranges)


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
        # _source, _pos, _length = _range
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
