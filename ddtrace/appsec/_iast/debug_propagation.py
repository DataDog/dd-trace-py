"""Debug utilities for tracking IAST taint propagation events.

This module provides two functions:
- taint_tracking_debug: append a structured event describing a source/propagation/sink
- export_and_clean_taint_tracking_debug: export the collected events to JSON and clear the buffer

It relies on get_info_frame() from `_stacktrace` to enrich events with file, line, function and class.
"""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.appsec._iast._stacktrace import get_info_frame
from ddtrace.appsec._iast._utils import _is_iast_propagation_debug_enabled
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# Global in-memory log of taint propagation events
# This is intentionally simple for debugging/inspection purposes only.
taint_log: List[Dict[str, Any]] = []


def _collect_parent_ids(candidate: Any = None, params: Any = None) -> List[str]:
    """Collect ids from candidate and params, supporting single values or iterables.

    Only Python builtins and simple iterables are supported. Non-iterable values are handled as singletons.
    """
    parents: List[str] = []

    def _extend_with(value: Any) -> None:
        parents.append(str(id(value)))

    # candidate can be a single value or an iterable
    if candidate is not None:
        if isinstance(candidate, (list, tuple)):
            for v in candidate:
                _extend_with(v)
        else:
            _extend_with(candidate)

    # params can be a single value or an iterable
    if params is not None:
        if isinstance(params, (list, tuple)):
            for v in params:
                _extend_with(v)
        else:
            _extend_with(params)

    return parents


def taint_tracking_debug(
    text_result: Any,
    text_candidate: Any | None = None,
    text_params: Any | None = None,
    action: str = "",
    type_propagation: Optional[str] = None,
    source_origin: Optional[str] = None,
) -> None:
    """Append a taint propagation debug event to the global log.

    This function is lightweight and best-effort: failures to enrich the event must never raise.
    """
    try:
        frame_info = get_info_frame() or (None, None, None, None)
        file_name, line_number, function_name, class_name = frame_info
    except Exception:
        file_name = line_number = function_name = class_name = None

    try:
        id_ = str(id(text_result))
        if action == "sink_point":
            id_ = "SP_" + id_
        event: Dict[str, Any] = {
            "id": id_,
            "parent_ids": _collect_parent_ids(text_candidate, text_params),
            "action": action,
            "string": str(text_result),
            "string_from": str(text_candidate),
            "string_params": str(text_params),
            "type_propagation": type_propagation,
            "file": file_name,
            "line": line_number,
            "function": function_name,
            "class": class_name,
            "origin": source_origin,
        }
        taint_log.append(event)
    except Exception:
        log.warning("taint_tracking_debug error", exc_info=True)


def export_and_clean_taint_tracking_debug() -> None:
    """Export the taint log to a JSON file and clear the in-memory buffer."""
    if _is_iast_propagation_debug_enabled():
        try:
            import json

            global taint_log
            with open("taint_tracking.json", "w") as f:
                json.dump(taint_log, f, indent=2)
            taint_log = []
        except Exception:
            log.warning("taint_tracking_debug error", exc_info=True)
