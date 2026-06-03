from typing import Any
from typing import Optional

from ddtrace.appsec._iast._taint_tracking._native import initialize_native_state  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native import ops  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native import reset_native_state  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_format import _format_aspect  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_helpers import _convert_escaped_text_to_tainted_text

# noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.aspect_helpers import are_all_text_all_ranges  # noqa: F401
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
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import OriginType  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import Source  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import TagMappingMode  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import VulnerabilityType  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import copy_and_shift_ranges_from_strings  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import copy_ranges_from_strings  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import get_range_by_hash  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import get_ranges as _native_get_ranges
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import is_tainted  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import origin_to_str  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import set_ranges  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import shift_taint_range  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import shift_taint_ranges  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import str_to_origin  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import taint_range as TaintRange  # noqa: F401
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# AIDEV-NOTE: _get_iast_context_id is imported lazily — a top-level import here
# circularly bootstraps via _iast_request_context_base -> _taint_tracking._context
# -> _taint_tracking/__init__.py. The cached module-global avoids the per-call
# import dance on this hot path.
_CACHE_GET_IAST_CONTEXT_ID = None


def get_ranges(string_input: Any, context_id: Optional[int] = None) -> Any:
    if context_id is None:
        global _CACHE_GET_IAST_CONTEXT_ID
        if _CACHE_GET_IAST_CONTEXT_ID is None:
            from ddtrace.appsec._iast._iast_request_context_base import _get_iast_context_id

            _CACHE_GET_IAST_CONTEXT_ID = _get_iast_context_id
        context_id = _CACHE_GET_IAST_CONTEXT_ID()
    return _native_get_ranges(string_input, context_id)


__all__ = [
    "OriginType",
    "Source",
    "TagMappingMode",
    "TaintRange",
    "VulnerabilityType",
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
    "_convert_escaped_text_to_tainted_text",
    "_format_aspect",
    "are_all_text_all_ranges",
    "as_formatted_evidence",
    "common_replace",
    "copy_and_shift_ranges_from_strings",
    "copy_ranges_from_strings",
    "get_range_by_hash",
    "get_ranges",
    "reset_native_state",
    "initialize_native_state",
    "is_tainted",
    "new_pyobject_id",
    "origin_to_str",
    "parse_params",
    "set_ranges",
    "set_ranges_on_splitted",
    "shift_taint_range",
    "shift_taint_ranges",
    "str_to_origin",
]
new_pyobject_id = ops.new_pyobject_id
taint_pyobject = ops.taint_pyobject
