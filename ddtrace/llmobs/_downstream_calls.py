"""Summaries of the outbound calls made while an LLMObs span is open.

Client spans (HTTP, database, gRPC) finish inside the tool that made them, so they can be folded into
the tool's LLMObs event instead of depending on the APM trace being kept.
"""

import re
from typing import Any
from typing import Iterable
from typing import Optional
from typing import Union
from urllib.parse import urlsplit
import weakref

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.threads import Lock
from ddtrace.llmobs._constants import PROPAGATED_PARENT_ID_KEY
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor


DOWNSTREAM_CALLS_CTX_KEY = "_ml_obs.downstream_calls"
MAX_DOWNSTREAM_CALLS = 50
_GRPC_METHOD_PATH = "grpc.method.path"
_ID_SEGMENT = re.compile(
    r"^(?:\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,})$", re.IGNORECASE
)


def _path_template(path: str) -> str:
    return "/".join("?" if _ID_SEGMENT.match(segment) else segment for segment in path.split("/")) or "/"


def _is_client_span(span: Span) -> bool:
    return span.span_type != SpanTypes.LLM and span.get_tag(SPAN_KIND) == SpanKind.CLIENT


def _call_key(span: Span) -> tuple[str, str, str, str, str]:
    """Return (kind, method, host, target, status). Query strings, credentials and SQL are never read."""
    url = span.get_tag(http.URL)
    if url:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = "%s:%d" % (host, parts.port)
        return (
            "http",
            span.get_tag(http.METHOD) or "",
            host,
            _path_template(parts.path),
            span.get_tag(http.STATUS_CODE) or "",
        )
    host = span.get_tag(net.TARGET_HOST) or span.get_tag(net.SERVER_ADDRESS) or ""
    grpc_method = span.get_tag(_GRPC_METHOD_PATH)
    if grpc_method:
        return ("grpc", "", host, grpc_method, "")
    system = span.get_tag(db.SYSTEM)
    if system:
        return ("db", system, host, span.name, "")
    return ("other", span.get_tag(COMPONENT) or "", host, span.name, "")


def _distinct(values: Iterable[str]) -> list[str]:
    return [v for v in dict.fromkeys(values) if v]


class DownstreamCallTracker:
    def __init__(self) -> None:
        self._lock = Lock()
        # LLMObs spans that can still receive calls, for client spans that ran in a thread pool and
        # only carry the LLMObs parent ID on their context rather than a parent span reference.
        self._open_spans: weakref.WeakValueDictionary[int, Span] = weakref.WeakValueDictionary()

    def on_llmobs_span_start(self, span: Span) -> None:
        self._open_spans[span.span_id] = span

    def on_span_finish(self, span: Span, active_llmobs: Optional[Union[Span, Context]]) -> None:
        # Count only the outermost client span, so requests -> urllib3 is one call.
        if not _is_client_span(span) or (span._parent is not None and _is_client_span(span._parent)):
            return
        owner = _get_nearest_llmobs_ancestor(span)
        if owner is None and isinstance(active_llmobs, Context):
            active_ctx = active_llmobs
            parent_id = active_ctx._meta.get(PROPAGATED_PARENT_ID_KEY)
            if parent_id and parent_id.isdigit():
                owner = self._open_spans.get(int(parent_id))
        if owner is None or owner.finished:
            return

        kind, method, host, target, status = _call_key(span)
        key = (kind, method, host, target, status)
        failed = bool(span.error) or status[:1] in ("4", "5")
        duration_ms = (span.duration_ns or 0) / 1e6
        with self._lock:
            calls = owner._get_ctx_item(DOWNSTREAM_CALLS_CTX_KEY)
            if calls is None:
                calls = {"entries": {}, "omitted": 0, "count": 0, "error_count": 0, "duration_ms": 0.0}
                owner._set_ctx_item(DOWNSTREAM_CALLS_CTX_KEY, calls)
            calls["count"] += 1
            calls["error_count"] += int(failed)
            calls["duration_ms"] += duration_ms
            entry = calls["entries"].get(key)
            if entry is None:
                if len(calls["entries"]) >= MAX_DOWNSTREAM_CALLS:
                    calls["omitted"] += 1
                    return
                entry = calls["entries"][key] = {
                    "kind": kind,
                    "method": method,
                    "host": host,
                    "target": target,
                    "status": status,
                    "count": 0,
                    "error_count": 0,
                    "duration_ms": 0.0,
                }
            entry["count"] += 1
            entry["error_count"] += int(failed)
            entry["duration_ms"] += duration_ms

    def pop(self, span: Span) -> Optional[dict[str, Any]]:
        """Stop tracking span and return its summary, if any calls were recorded.

        The per-call list is for display. Hosts, targets, statuses and endpoints are also reported as
        flat lists so spans can be filtered and grouped by them, and the totals are span-level scalars
        that aggregate correctly; values inside the list of calls do not.
        """
        with self._lock:
            self._open_spans.pop(span.span_id, None)
            calls = span._get_ctx_item(DOWNSTREAM_CALLS_CTX_KEY)
            if not calls:
                return None
            span._set_ctx_item(DOWNSTREAM_CALLS_CTX_KEY, None)
        entries = list(calls["entries"].values())
        for entry in entries:
            entry["duration_ms"] = round(entry["duration_ms"], 3)
        summary: dict[str, Any] = {
            "calls": entries,
            "hosts": _distinct(e["host"] for e in entries),
            "targets": _distinct(e["target"] for e in entries),
            "statuses": _distinct(e["status"] for e in entries),
            "endpoints": _distinct(" ".join(filter(None, (e["method"], e["host"] + e["target"]))) for e in entries),
            "total_count": calls["count"],
            "total_error_count": calls["error_count"],
            "total_duration_ms": round(calls["duration_ms"], 3),
        }
        if calls["omitted"]:
            summary["omitted_count"] = calls["omitted"]
        return summary
