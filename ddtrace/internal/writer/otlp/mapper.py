from __future__ import annotations

from itertools import islice
from typing import TYPE_CHECKING
from typing import Any

from ddtrace.constants import ERROR_MSG
from ddtrace.ext import SpanKind as DDSpanKind
from ddtrace.version import __version__ as _dd_version


if TYPE_CHECKING:
    from ddtrace.trace import Span

# OTLP SpanKind (enum value)
OTLP_SPAN_KIND_UNSPECIFIED = 0
OTLP_SPAN_KIND_INTERNAL = 1
OTLP_SPAN_KIND_SERVER = 2
OTLP_SPAN_KIND_CLIENT = 3
OTLP_SPAN_KIND_PRODUCER = 4
OTLP_SPAN_KIND_CONSUMER = 5

# OTLP StatusCode
OTLP_STATUS_CODE_UNSET = 0
OTLP_STATUS_CODE_OK = 1
OTLP_STATUS_CODE_ERROR = 2

# Limits
MAX_ATTRIBUTES_PER_SPAN = 128
MAX_EVENTS_PER_SPAN = 32
MAX_ATTRIBUTES_PER_EVENT = 16


def _trace_id_to_hex(trace_id: int) -> str:
    """Convert DD trace_id (64 or 128 bit) to OTLP 32-char hex (128-bit)."""
    mask_128 = (1 << 128) - 1
    return f"{trace_id & mask_128:032x}"


def _span_id_to_hex(span_id: int) -> str:
    """Convert DD span_id to OTLP 16-char hex (64-bit)."""
    return f"{span_id & ((1 << 64) - 1):016x}"


def _dd_span_type_to_otlp_kind(span_type: str | None) -> int:
    """Map Datadog span_type to OTLP SpanKind."""
    if not span_type:
        return OTLP_SPAN_KIND_INTERNAL
    t = span_type.lower()
    if t in (DDSpanKind.SERVER, "web", "http"):
        return OTLP_SPAN_KIND_SERVER
    if t == DDSpanKind.CLIENT:
        return OTLP_SPAN_KIND_CLIENT
    if t == DDSpanKind.PRODUCER:
        return OTLP_SPAN_KIND_PRODUCER
    if t == DDSpanKind.CONSUMER:
        return OTLP_SPAN_KIND_CONSUMER
    return OTLP_SPAN_KIND_INTERNAL


def _span_status(span: Span) -> dict[str, Any]:
    """
    Build OTLP Status from Datadog span.
    code = STATUS_CODE_ERROR if span.error == 1 else STATUS_CODE_UNSET.
    message = span._meta["error.message"] or span._meta["error.msg"].
    """
    error_flag = getattr(span, "error", 0)
    if error_flag == 1:
        code = OTLP_STATUS_CODE_ERROR
    else:
        code = OTLP_STATUS_CODE_UNSET
    meta = getattr(span, "_meta", None) or {}
    # DD uses ERROR_MSG ("error.message"); RFC uses error.msg
    message = meta.get(ERROR_MSG) or meta.get("error.msg") or ""
    status = {"code": code}
    if message:
        status["message"] = str(message)[:8192]  # truncate long messages
    return status


def _attribute_value(v: Any) -> dict[str, Any]:
    """OTLP KeyValue value: one of stringValue, boolValue, intValue, doubleValue, bytesValue, arrayValue."""
    if v is None:
        return {"stringValue": ""}
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        # OTLP array of values
        arr = []
        for item in v:
            if isinstance(item, str):
                arr.append({"stringValue": item})
            elif isinstance(item, bool):
                arr.append({"boolValue": item})
            elif isinstance(item, int):
                arr.append({"intValue": str(item)})
            elif isinstance(item, float):
                arr.append({"doubleValue": item})
            else:
                arr.append({"stringValue": str(item)})
        return {"arrayValue": {"values": arr}}
    s = str(v)
    # Truncate very long string values (OTLP/backend limits)
    if len(s) > 8192:
        s = s[:8192] + "..."
    return {"stringValue": s}


def _span_attributes(span: Span) -> tuple[list[dict[str, Any]], int]:
    """Build OTLP attributes from span meta and metrics. Returns (attributes, dropped_count)."""
    attrs: list[dict[str, Any]] = []
    meta = getattr(span, "_meta", None) or {}
    metrics = getattr(span, "_metrics", None) or {}
    n = 0
    for k, v in meta.items():
        if n >= MAX_ATTRIBUTES_PER_SPAN:
            break
        if v is None or (isinstance(v, str) and len(v) == 0):
            continue
        attrs.append({"key": k, "value": _attribute_value(v)})
        n += 1
    for k, v in metrics.items():
        if n >= MAX_ATTRIBUTES_PER_SPAN:
            break
        if v is None:
            continue
        attrs.append({"key": k, "value": _attribute_value(v)})
        n += 1
    total = len(meta) + len(metrics)
    dropped = max(0, total - MAX_ATTRIBUTES_PER_SPAN)
    return attrs, dropped


def _span_events(span: Span) -> tuple[list[dict[str, Any]], int]:
    """Build OTLP events from span events if present. Returns (events, dropped_events_count)."""
    events = getattr(span, "_events", None) or []
    if not events:
        return [], 0
    result = []
    for ev in islice(events, MAX_EVENTS_PER_SPAN):
        name = getattr(ev, "name", "event")
        time_ns = getattr(ev, "time_unix_nano", None)
        if time_ns is None:
            time_ns = getattr(span, "start_ns", 0) or 0
        attrs = getattr(ev, "attributes", None) or {}
        otlp_ev = {
            "time_unix_nano": str(time_ns),
            "name": name,
            "attributes": [
                {"key": k, "value": _attribute_value(v)} for k, v in islice(attrs.items(), MAX_ATTRIBUTES_PER_EVENT)
            ],
        }
        result.append(otlp_ev)
    dropped = max(0, len(events) - MAX_EVENTS_PER_SPAN)
    return result, dropped


def _map_span(span: Span) -> dict[str, Any]:
    """Map one Datadog span to OTLP Span dict (snake_case, nano timestamps)."""
    trace_id_hex = _trace_id_to_hex(span.trace_id)
    span_id_hex = _span_id_to_hex(span.span_id)
    parent_id = getattr(span, "parent_id", None)
    parent_span_id_hex = _span_id_to_hex(parent_id) if parent_id is not None else ""

    start_ns = getattr(span, "start_ns", None) or 0
    duration_ns = getattr(span, "duration_ns", None) or 0
    end_time_unix_nano = start_ns + duration_ns

    kind = _dd_span_type_to_otlp_kind(getattr(span, "span_type", None))
    attrs, dropped_attrs = _span_attributes(span)
    events, dropped_events = _span_events(span)

    status = _span_status(span)

    return {
        "trace_id": trace_id_hex,
        "span_id": span_id_hex,
        "parent_span_id": parent_span_id_hex or None,
        "name": span.name or "span",
        "kind": kind,
        "start_time_unix_nano": str(start_ns),
        "end_time_unix_nano": str(end_time_unix_nano),
        "attributes": attrs,
        "events": events if events else None,
        "dropped_attributes_count": dropped_attrs,
        "dropped_events_count": dropped_events,
        "status": status,
    }


def _resource_attributes(
    service_name: str,
    env: str | None,
    version: str | None,
    runtime_id: str | None = None,
    git_commit_sha: str | None = None,
    git_repository_url: str | None = None,
) -> list[dict[str, Any]]:
    """Build OTLP resource attributes (service.name, deployment.environment, etc.)."""
    attrs = [
        {"key": "service.name", "value": _attribute_value(service_name or "unknown")},
        {"key": "telemetry.sdk.name", "value": _attribute_value("datadog")},
        {"key": "telemetry.sdk.language", "value": _attribute_value("python")},
        {"key": "telemetry.sdk.version", "value": _attribute_value(_dd_version)},
    ]
    if env:
        attrs.append({"key": "deployment.environment", "value": _attribute_value(env)})
    if version:
        attrs.append({"key": "service.version", "value": _attribute_value(version)})
    if runtime_id:
        attrs.append({"key": "runtime-id", "value": _attribute_value(runtime_id)})
    if git_commit_sha:
        attrs.append({"key": "git.commit.sha", "value": _attribute_value(git_commit_sha)})
    if git_repository_url:
        attrs.append({"key": "git.repository_url", "value": _attribute_value(git_repository_url)})
    return attrs


def dd_trace_to_otlp_request(
    spans: list[Span],
    service_name: str | None = None,
    env: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """
    Convert one Datadog trace (list of spans) to OTLP ExportTraceServiceRequest structure.

    Uses a single instrumentation scope "datadog". Resource gets service.name and
    telemetry.sdk.* attributes. Each span is mapped with ids, times, kind, attributes, status.

    :param spans: List of finished DD spans (one trace).
    :param service_name: Service name for resource (default from first span).
    :param env: DD_ENV / deployment.environment.
    :param version: DD_VERSION / service.version.
    :returns: Dict suitable for JSON serialization (snake_case, nano timestamps).
    """
    if not spans:
        return {"resource_spans": []}

    first = spans[0]
    service = service_name or getattr(first, "service", None) or "unknown"
    otlp_spans = [_map_span(s) for s in spans]

    # Optional resource attributes (when available)
    runtime_id = None
    git_commit_sha = None
    git_repository_url = None
    try:
        from ddtrace.internal.runtime import get_runtime_id

        runtime_id = get_runtime_id()
    except Exception:
        pass
    try:
        from ddtrace.internal import gitmetadata

        git_repository_url, git_commit_sha, _ = gitmetadata.get_git_tags()
    except Exception:
        pass

    resource = {
        "attributes": _resource_attributes(
            service,
            env,
            version,
            runtime_id=runtime_id,
            git_commit_sha=git_commit_sha or None,
            git_repository_url=git_repository_url or None,
        ),
        "dropped_attributes_count": 0,
    }
    scope_spans = [
        {
            "scope": {"name": "datadog", "version": _dd_version},
            "spans": otlp_spans,
            "schema_url": "",
        }
    ]
    resource_span = {
        "resource": resource,
        "scope_spans": scope_spans,
        "schema_url": "",
    }
    return {"resource_spans": [resource_span]}
