"""Snapshot support for traces emitted with OTel semantics enabled.

DD_TRACE_OTEL_SEMANTICS_ENABLED=true makes the tracer export over OTLP, but the ddapm test agent
only snapshots Datadog-protocol traces. The test agent does store OTLP payloads on its OTLP HTTP
port, so snapshot_context(otel_semantics=True) fetches them from there, normalizes the values
that change between runs, and compares the result with tests/snapshots/<token>.json itself.
The snapshot keeps the OTLP shape (resource, scope, typed attribute values, kind, status), so it
shows exactly what was exported.

Each test uses its own session token, sent as an OTLP export header, so tests do not share data.
As with agent-side snapshots, a missing file is generated on the first run and must be reviewed and
committed; delete the file to regenerate it. Under CI a missing file fails the test.
"""

import difflib
import json
import os
from pathlib import Path
import time
from typing import Any
from typing import Iterable
from typing import Optional
from urllib import parse
from urllib import request as urlrequest


SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

OTLP_PORT = 4318
TRACES_PATH = "/v1/traces"
SESSION_TOKEN_HEADER = "X-Datadog-Test-Session-Token"

# Attributes that change between runs or machines. They mirror the agent's default snapshot
# ignores; tests extend them through the ignores argument.
DEFAULT_IGNORED_ATTRIBUTES = frozenset(
    {
        "_dd.git.commit.sha",
        "_dd.git.repository_url",
        "_dd.p.tid",
        "_dd.parent_id",
        "_dd.tags.process",
        "_dd.tracer_kr",
        "host.name",
        "process.pid",
        "process_id",
        "runtime-id",
        "service.instance.id",
        "system.pid",
        "telemetry.sdk.version",
        "tracestate",
    }
)

# Fields whose values are random or tied to the tracer version.
DEFAULT_IGNORED_FIELDS = frozenset({"trace_state", "version"})


def otlp_base_url() -> str:
    url = os.environ.get("DD_TEST_OTLP_URL")
    if url:
        return url.rstrip("/")
    # In CI the agent is reachable by service name; only the port differs from the trace port.
    agent_url = os.environ.get("DD_TRACE_AGENT_URL")
    host = parse.urlparse(agent_url).hostname if agent_url else None
    return f"http://{host or 'localhost'}:{OTLP_PORT}"


def otel_semantics_env(token: str) -> dict[str, str]:
    """Environment that enables OTel semantics and exports the resulting OTLP traces to the test agent."""
    # The token must match the one the rest of the snapshot flow (and the tracer's own session
    # header) uses, so it is sent unchanged. Commas separate header entries and cannot be escaped.
    if "," in token:
        raise ValueError(f"the snapshot token cannot contain a comma: {token!r}")
    return {
        "DD_TRACE_OTEL_SEMANTICS_ENABLED": "true",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{otlp_base_url()}{TRACES_PATH}",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"{SESSION_TOKEN_HEADER}={token}",
    }


def _span_count(requests: Iterable[dict[str, Any]]) -> int:
    return sum(
        len(scope_spans.get("spans", []))
        for request in requests
        for resource_spans in request.get("resource_spans", [])
        for scope_spans in resource_spans.get("scope_spans", [])
    )


def _trace_count(requests: Iterable[dict[str, Any]]) -> int:
    return len(
        {
            span["trace_id"]
            for request in requests
            for resource_spans in request.get("resource_spans", [])
            for scope_spans in resource_spans.get("scope_spans", [])
            for span in scope_spans.get("spans", [])
        }
    )


def fetch_otlp_requests(
    token: str, timeout: float = 30.0, settle: float = 1.0, min_traces: Optional[int] = None
) -> list[dict[str, Any]]:
    """Poll the agent until OTLP spans arrive and the span count stops changing.

    The application exports on flush or shutdown, so data can show up shortly after the request
    that produced it returns. With min_traces, polling stops once that many traces have arrived,
    matching wait_for_num_traces on the Datadog-protocol snapshot path.
    """
    query = parse.urlencode({"test_session_token": token})
    url = f"{otlp_base_url()}/test/session/traces?{query}"
    deadline = time.monotonic() + timeout
    requests: list[dict[str, Any]] = []
    last_count = 0
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        with urlrequest.urlopen(url, timeout=10) as response:
            requests = json.loads(response.read())
        if min_traces is not None:
            if _trace_count(requests) >= min_traces:
                break
            time.sleep(0.25)
            continue
        count = _span_count(requests)
        now = time.monotonic()
        if count != last_count:
            last_count, stable_since = count, now
        elif count > 0 and now - stable_since >= settle:
            break
        time.sleep(0.25)
    return requests


def _ignored_names(ignores: Iterable[str]) -> frozenset[str]:
    # Accept the agent-style "meta.key" / "metrics.key" spelling as well as plain attribute keys.
    names = set()
    for ignore in ignores:
        names.add(ignore)
        for prefix in ("meta.", "metrics."):
            if ignore.startswith(prefix):
                names.add(ignore[len(prefix) :])
    return frozenset(names)


def _clean(node: Any, ignored_attributes: frozenset[str], ignored_fields: frozenset[str]) -> Any:
    if isinstance(node, list):
        return [_clean(item, ignored_attributes, ignored_fields) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned = {}
    for key, value in node.items():
        if key in ignored_fields:
            continue
        if key == "attributes" and isinstance(value, list):
            value = sorted(
                (attribute for attribute in value if attribute.get("key") not in ignored_attributes),
                key=lambda attribute: attribute.get("key", ""),
            )
        cleaned[key] = _clean(value, ignored_attributes, ignored_fields)
    return cleaned


_ID_AND_TIME_FIELDS = frozenset({"trace_id", "span_id", "parent_span_id", "start_time_unix_nano", "end_time_unix_nano"})


def _content_key(span: dict[str, Any], ignored_attributes: frozenset[str], ignored_fields: frozenset[str]) -> str:
    content = {key: value for key, value in span.items() if key not in _ID_AND_TIME_FIELDS}
    content["events"] = [
        {key: value for key, value in event.items() if key != "time_unix_nano"} for event in span.get("events", [])
    ]
    content["links"] = [
        {key: value for key, value in link.items() if key not in ("trace_id", "span_id")}
        for link in span.get("links", [])
    ]
    return json.dumps(_clean(content, ignored_attributes, ignored_fields), sort_keys=True)


def normalize_otlp_requests(requests: Iterable[dict[str, Any]], ignores: Iterable[str] = ()) -> dict[str, Any]:
    """Make OTLP payloads from different runs comparable.

    Ids become ordinal placeholders assigned from span content and parent/child links (not start
    times), timestamps are validated and replaced, attribute lists are sorted, and ignored attributes
    and fields are dropped.
    """
    ignored_attributes = DEFAULT_IGNORED_ATTRIBUTES | _ignored_names(ignores)
    # Test ignores name attributes only, so an ignore that shares a name with an OTLP field
    # (such as "name" or "kind") cannot drop that field from the snapshot.
    ignored_fields = DEFAULT_IGNORED_FIELDS

    # Work on a copy so the caller's payload is left untouched.
    resource_spans = json.loads(json.dumps([rs for request in requests for rs in request.get("resource_spans", [])]))
    # Export boundaries are transport details, so combine groups with equivalent cleaned metadata.
    groups: dict[str, dict[str, Any]] = {}
    scope_groups: dict[str, dict[str, dict[str, Any]]] = {}
    for rs in resource_spans:
        metadata = _clean(
            {key: value for key, value in rs.items() if key != "scope_spans"}, ignored_attributes, ignored_fields
        )
        resource_key = json.dumps(metadata, sort_keys=True)
        group = groups.setdefault(resource_key, {**metadata, "scope_spans": []})
        grouped_scopes = scope_groups.setdefault(resource_key, {})
        for scope in rs.get("scope_spans", []):
            metadata = _clean(
                {key: value for key, value in scope.items() if key != "spans"}, ignored_attributes, ignored_fields
            )
            scope_key = json.dumps(metadata, sort_keys=True)
            if scope_key not in grouped_scopes:
                grouped_scopes[scope_key] = {**metadata, "spans": []}
                group["scope_spans"].append(grouped_scopes[scope_key])
            grouped_scopes[scope_key]["spans"].extend(scope.get("spans", []))
    resource_spans = list(groups.values())
    scopes = [ss for rs in resource_spans for ss in rs.get("scope_spans", [])]
    spans = [span for scope in scopes for span in scope.get("spans", [])]
    # Identical span trees can come from different resources or scopes; their content breaks the tie.
    owners: dict[int, str] = {}
    for rs in resource_spans:
        resource = _clean(
            {key: value for key, value in rs.items() if key != "scope_spans"}, ignored_attributes, ignored_fields
        )
        for scope in rs.get("scope_spans", []):
            owner = json.dumps(
                [
                    resource,
                    _clean(
                        {key: value for key, value in scope.items() if key != "spans"},
                        ignored_attributes,
                        ignored_fields,
                    ),
                ],
                sort_keys=True,
            )
            for span in scope.get("spans", []):
                owners[id(span)] = owner

    # Placeholders follow span content and parent links, never start times, so spans that run
    # concurrently get the same placeholders on every run.
    content_keys = {
        id(span): json.dumps([owners[id(span)], _content_key(span, ignored_attributes, ignored_fields)])
        for span in spans
    }

    def identity(span: dict[str, Any]) -> tuple[str, str]:
        return span["trace_id"], span["span_id"]

    by_span_id = {identity(span): span for span in spans}
    children: dict[tuple[str, str], list[dict[str, Any]]] = {}
    roots_by_trace: dict[str, list[dict[str, Any]]] = {}
    unresolved_children: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for span in spans:
        parent = (span["trace_id"], span.get("parent_span_id", ""))
        if parent in by_span_id:
            children.setdefault(parent, []).append(span)
        else:
            roots_by_trace.setdefault(span["trace_id"], []).append(span)
            if span.get("parent_span_id"):
                unresolved_children.setdefault(parent, []).append(span)

    subtree_keys: dict[int, str] = {}

    def subtree_key(span: dict[str, Any]) -> str:
        key = subtree_keys.get(id(span))
        if key is None:
            child_keys = sorted(subtree_key(child) for child in children.get(identity(span), []))
            key = subtree_keys[id(span)] = json.dumps([content_keys[id(span)], child_keys])
        return key

    # Refine content labels with both directions of links. A link to one of two otherwise
    # identical siblings must distinguish that target without using its random identifier.
    incoming: dict[tuple[str, str], list[tuple[dict[str, Any], str]]] = {}
    outgoing: dict[tuple[str, str], list[tuple[dict[str, Any], str]]] = {}
    for span in spans:
        for link in span.get("links", []):
            target = by_span_id.get(identity(link))
            if target is not None:
                metadata = json.dumps(
                    _clean(
                        {key: value for key, value in link.items() if key not in ("trace_id", "span_id")},
                        ignored_attributes,
                        ignored_fields,
                    ),
                    sort_keys=True,
                )
                outgoing.setdefault(identity(span), []).append((target, metadata))
                incoming.setdefault(identity(target), []).append((span, metadata))

    def rank(keys: dict[int, str]) -> dict[int, int]:
        ranks = {key: index for index, key in enumerate(sorted(set(keys.values())))}
        return {span_id: ranks[key] for span_id, key in keys.items()}

    labels = rank({id(span): subtree_key(span) for span in spans})
    for _ in range(len(spans)):
        keys = {}
        for span in spans:
            parent_key = (span["trace_id"], span.get("parent_span_id", ""))
            parent = by_span_id.get(parent_key)
            if parent is not None:
                parent_label = ["resolved", labels[id(parent)]]
            elif span.get("parent_span_id"):
                parent_label = ["unresolved", sorted(labels[id(child)] for child in unresolved_children[parent_key])]
            else:
                parent_label = ["root"]
            keys[id(span)] = json.dumps(
                [
                    labels[id(span)],
                    parent_label,
                    sorted(labels[id(child)] for child in children.get(identity(span), [])),
                    sorted((metadata, labels[id(target)]) for target, metadata in outgoing.get(identity(span), [])),
                    sorted((metadata, labels[id(source)]) for source, metadata in incoming.get(identity(span), [])),
                ]
            )
        refined = rank(keys)
        stable = len(set(refined.values())) == len(set(labels.values()))
        labels = refined
        if stable:
            break

    def ordering_key(span: dict[str, Any]) -> tuple[str, int]:
        return subtree_key(span), labels[id(span)]

    trace_ids: dict[str, str] = {}
    span_ids: dict[tuple[str, str], str] = {}

    def assign(span: dict[str, Any]) -> None:
        trace_ids.setdefault(span["trace_id"], f"trace_{len(trace_ids) + 1}")
        span_ids.setdefault(identity(span), f"span_{len(span_ids) + 1}")
        for child in sorted(children.get(identity(span), []), key=ordering_key):
            assign(child)

    for _, roots in sorted(roots_by_trace.items(), key=lambda item: sorted(ordering_key(root) for root in item[1])):
        for root in sorted(roots, key=ordering_key):
            assign(root)
    ordered_spans = sorted(spans, key=lambda span: int(span_ids[identity(span)].split("_")[1]))
    for span in ordered_spans:
        parent = span.get("parent_span_id")
        if parent:
            span_ids.setdefault((span["trace_id"], parent), f"span_{len(span_ids) + 1}")
    spans = ordered_spans

    for span in spans:
        start = int(span["start_time_unix_nano"])
        end = int(span["end_time_unix_nano"])
        # Raised explicitly because this module is not rewritten by pytest, so an assert would be a no-op
        # under PYTHONOPTIMIZE.
        if not 0 < start <= end:
            raise AssertionError(f"span {span['name']!r} has an invalid time range: {start}..{end}")
        span["start_time_unix_nano"] = "<start_time_unix_nano>"
        span["end_time_unix_nano"] = "<end_time_unix_nano>"
        span_key = identity(span)
        span["trace_id"] = trace_ids[span_key[0]]
        span["span_id"] = span_ids[span_key]
        if span.get("parent_span_id"):
            span["parent_span_id"] = span_ids[(span_key[0], span["parent_span_id"])]
        for event in span.get("events", []):
            event["time_unix_nano"] = "<time_unix_nano>"
        # Links can point at spans outside the payload, so unknown ids get placeholders as well.
        for link in span.get("links", []):
            link_key = identity(link)
            link["trace_id"] = trace_ids.setdefault(link["trace_id"], f"trace_{len(trace_ids) + 1}")
            link["span_id"] = span_ids.setdefault(link_key, f"span_{len(span_ids) + 1}")
        if "links" in span:
            span["links"].sort(key=lambda link: json.dumps(link, sort_keys=True))

    for scope in scopes:
        scope["spans"].sort(key=lambda span: int(span["span_id"].split("_")[1]))

    normalized = _clean(resource_spans, ignored_attributes, ignored_fields)
    for rs in normalized:
        rs["scope_spans"].sort(key=lambda scope: json.dumps(scope, sort_keys=True))
    normalized.sort(key=lambda rs: json.dumps(rs, sort_keys=True))
    return {"resource_spans": normalized}


def assert_matches_snapshot(normalized: dict[str, Any], snapshot_file: Path) -> None:
    rendered = json.dumps(normalized, indent=2, sort_keys=True) + "\n"
    if not snapshot_file.exists():
        if os.environ.get("CI") == "true":
            raise AssertionError(
                f"OTLP snapshot file '{snapshot_file}' not found. Was it checked into source control? "
                "It is generated automatically when running outside CI."
            )
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        snapshot_file.write_text(rendered)
        return

    expected = snapshot_file.read_text()
    if json.loads(expected) != normalized:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                rendered.splitlines(keepends=True),
                fromfile=f"expected ({snapshot_file.name})",
                tofile="received",
            )
        )
        raise AssertionError(f"OTLP snapshot mismatch for '{snapshot_file}':\n{diff}")


def assert_otel_semantics_snapshot(
    token: str,
    ignores: Iterable[str] = (),
    timeout: float = 30.0,
    snapshot_dir: Path = SNAPSHOT_DIR,
    wait_for_num_traces: Optional[int] = None,
) -> None:
    """Fetch the OTLP traces exported under token and compare them with their snapshot file."""
    requests = fetch_otlp_requests(token, timeout=timeout, min_traces=wait_for_num_traces)
    # Fewer traces than requested fails, as on the Datadog-protocol path, instead of snapshotting a
    # partial export.
    if wait_for_num_traces and _trace_count(requests) < wait_for_num_traces:
        raise AssertionError(
            f"expected {wait_for_num_traces} OTLP trace(s) for session '{token}', got {_trace_count(requests)}"
        )
    # wait_for_num_traces=0 asserts that nothing was exported, as on the Datadog-protocol path.
    if wait_for_num_traces != 0 and _span_count(requests) <= 0:
        raise AssertionError(f"no OTLP spans received by the test agent for session '{token}'")
    # Keep the session token unchanged for the agent, but encode path separators in the filename.
    filename = parse.quote(token, safe="[]")
    assert_matches_snapshot(normalize_otlp_requests(requests, ignores), snapshot_dir / f"{filename}.json")
