"""Summaries of the outbound calls made while an LLMObs span is open.

Client spans (HTTP, database, gRPC) finish inside the tool that made them, so they can be folded into
the tool's LLMObs event instead of depending on the APM trace being kept.
"""

import re
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Optional
from urllib.parse import urlsplit
import weakref

from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.threads import Lock
from ddtrace.llmobs._span import _LLMObsSpan


DOWNSTREAM_CALLS_CTX_KEY = "_ml_obs.downstream_calls"
MAX_DOWNSTREAM_CALLS = 50
# Bounds each recorded host and target, so a long path from an untrusted server can't inflate the event.
MAX_DOWNSTREAM_FIELD_LENGTH = 256
_GRPC_METHOD_PATH = "grpc.method.path"
_GRPC_STATUS_CODE = "grpc.status.code"
_ID_SEGMENT = re.compile(
    r"^(?:\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,})$", re.IGNORECASE
)
# Marks a summary that was already exported, so calls finishing afterwards are dropped.
_CLOSED: dict[str, Any] = {}


def _is_opaque_segment(segment: str) -> bool:
    """Identifiers and secrets: numeric, UUID and long hex IDs, emails and encoded values, and tokens."""
    if _ID_SEGMENT.match(segment) or "@" in segment or "%" in segment:
        return True
    return len(segment) >= 24 and any(c.isdigit() for c in segment) and any(c.isalpha() for c in segment)


def _path_template(path: str) -> str:
    return "/".join("?" if _is_opaque_segment(segment) else segment for segment in path.split("/")) or "/"


def _is_client_span(span: _LLMObsSpan) -> bool:
    return span.span_type != SpanTypes.LLM and span.get_tag(SPAN_KIND) == SpanKind.CLIENT


def _owner(span: _LLMObsSpan) -> tuple[Optional[_LLMObsSpan], bool]:
    """Return (nearest LLMObs ancestor, whether span is nested inside another client span).

    A client span inside another one, even through intermediate spans, is part of that call.
    """
    parent = span._parent
    while parent is not None:
        if parent.span_type == SpanTypes.LLM:
            return parent, False
        if parent.get_tag(SPAN_KIND) == SpanKind.CLIENT:
            return None, True
        parent = parent._parent
    return None, False


def _first_tag(span: _LLMObsSpan, *keys: str) -> str:
    for key in keys:
        value = span.get_tag(key)
        if value:
            return str(value)
    return ""


def _port(span: _LLMObsSpan) -> str:
    # Integrations record ports as metrics; a few set them as tags.
    for key in (net.TARGET_PORT, otel_http.SERVER_PORT):
        value = span.get_metric(key)
        if value is not None:
            return str(int(value))
    return _first_tag(span, net.TARGET_PORT, otel_http.SERVER_PORT)


def _destination(span: _LLMObsSpan) -> str:
    host = _first_tag(span, net.TARGET_NAME, net.TARGET_HOST, net.SERVER_ADDRESS)
    port = _port(span)
    if host and port:
        host = "[%s]:%s" % (host, port) if ":" in host else "%s:%s" % (host, port)
    return host


def _call_key(span: _LLMObsSpan) -> tuple[str, str, str, str, str]:
    """Return (kind, method, host, target, status). Query strings, credentials and SQL are never read."""
    host = _destination(span)
    url = _first_tag(span, http.URL, otel_http.URL_FULL)
    if url:
        target = ""
        try:
            parts = urlsplit(url)
            # netloc keeps IPv6 brackets and the port; drop any credentials before the "@".
            host = parts.netloc.rpartition("@")[2] or host
            target = _path_template(parts.path)
        except ValueError:
            pass
        return (
            "http",
            _first_tag(span, http.METHOD, otel_http.REQUEST_METHOD),
            host,
            target,
            _first_tag(span, http.STATUS_CODE, otel_http.RESPONSE_STATUS_CODE),
        )
    grpc_method = span.get_tag(_GRPC_METHOD_PATH)
    if grpc_method:
        return ("grpc", "", host, grpc_method, span.get_tag(_GRPC_STATUS_CODE) or "")
    system = span.get_tag(db.SYSTEM)
    if system:
        return ("db", system, host, span.name, "")
    return ("other", span.get_tag(COMPONENT) or "", host, span.name, "")


def _is_failed(kind: str, status: str) -> bool:
    if not status:
        return False
    if kind == "grpc":
        return status not in ("0", "OK", "StatusCode.OK")
    return status[:1] in ("4", "5")


def _endpoint(entry: dict[str, Any]) -> str:
    """'METHOD host/path', or 'METHOD host target' when the target isn't a path, so they stay distinct."""
    host, target = entry["host"], entry["target"]
    separator = " " if host and target and not target.startswith("/") else ""
    return " ".join(filter(None, (entry["method"], host + separator + target)))


def _distinct(values: Iterable[str]) -> list[str]:
    return [v for v in dict.fromkeys(values) if v]


class DownstreamCallTracker:
    def __init__(self) -> None:
        self._lock = Lock()
        # LLMObs spans that can still receive calls, for client spans that ran in a thread pool and
        # only carry the LLMObs parent ID on their context rather than a parent span reference.
        self._open_spans: weakref.WeakValueDictionary[int, _LLMObsSpan] = weakref.WeakValueDictionary()

    def on_llmobs_span_start(self, span: _LLMObsSpan) -> None:
        self._open_spans[span.span_id] = span

    def on_span_finish(self, span: _LLMObsSpan, propagated_parent_id: Callable[[], Optional[str]]) -> None:
        """Record span against its LLMObs owner if it is an outbound client call.

        propagated_parent_id is only called for client spans with no LLMObs ancestor, since looking up
        the active context is not free and this runs for every finished span.
        """
        if not _is_client_span(span):
            return
        owner, nested = _owner(span)
        if nested:
            return
        if owner is None:
            parent_id = propagated_parent_id()
            if not parent_id or not parent_id.isdigit():
                return
            owner = self._open_spans.get(int(parent_id))
            if owner is None:
                return

        kind, method, host, target, status = _call_key(span)
        host, target = host[:MAX_DOWNSTREAM_FIELD_LENGTH], target[:MAX_DOWNSTREAM_FIELD_LENGTH]
        key = (kind, method, host, target, status)
        failed = bool(span.error) or _is_failed(kind, status)
        duration_ms = (span.duration_ns or 0) / 1e6
        with self._lock:
            calls = owner._get_ctx_item(DOWNSTREAM_CALLS_CTX_KEY)
            if calls is _CLOSED:
                return
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

    def pop(self, span: _LLMObsSpan) -> Optional[dict[str, Any]]:
        """Stop tracking span and return its summary, if any calls were recorded.

        The per-call list is for display. Hosts, targets, statuses and endpoints are also reported as
        flat lists so spans can be filtered and grouped by them, and the totals are span-level scalars
        that aggregate correctly; values inside the list of calls do not.
        """
        with self._lock:
            self._open_spans.pop(span.span_id, None)
            calls = span._get_ctx_item(DOWNSTREAM_CALLS_CTX_KEY)
            span._set_ctx_item(DOWNSTREAM_CALLS_CTX_KEY, _CLOSED)
        if not calls:
            return None
        entries = list(calls["entries"].values())
        for entry in entries:
            entry["duration_ms"] = round(entry["duration_ms"], 3)
        summary: dict[str, Any] = {
            "calls": entries,
            "hosts": _distinct(e["host"] for e in entries),
            "targets": _distinct(e["target"] for e in entries),
            "statuses": _distinct(e["status"] for e in entries),
            "endpoints": _distinct(_endpoint(e) for e in entries),
            "total_count": calls["count"],
            "total_error_count": calls["error_count"],
            "total_duration_ms": round(calls["duration_ms"], 3),
        }
        if calls["omitted"]:
            summary["omitted_count"] = calls["omitted"]
        return summary
