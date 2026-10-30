"""Tests for summarizing a tool span's outbound client spans into its LLMObs event."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.futures.patch import patch as patch_futures
from ddtrace.contrib.internal.futures.patch import unpatch as unpatch_futures
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.llmobs._downstream_calls import MAX_DOWNSTREAM_CALLS
from ddtrace.llmobs._utils import _annotate_llmobs_span_data


def _http_call(tracer, url, method="GET", status="200", component="httpx"):
    with tracer.trace("%s.request" % component, span_type="http") as span:
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag("component", component)
        span.set_tag("http.url", url)
        span.set_tag("http.method", method)
        span.set_tag("http.status_code", status)
    return span


def _downstream_calls(event):
    return event["meta"]["metadata"].get("_dd", {}).get("downstream_calls")


def _event(llmobs_events, name):
    return next(e for e in llmobs_events if e["name"] == name)


def test_http_calls_are_grouped_by_endpoint(llmobs, llmobs_events, tracer):
    with llmobs.tool("execute_code"):
        _http_call(tracer, "https://api.example.com/v1/orders/123")
        _http_call(tracer, "https://user:pw@api.example.com/v1/orders/456?token=secret")
        _http_call(tracer, "https://api.example.com/search", method="POST", status="429")

    calls = _downstream_calls(_event(llmobs_events, "execute_code"))
    assert calls is not None
    entries = sorted(calls["calls"], key=lambda e: e["target"])
    assert [(e["method"], e["host"], e["target"], e["status"], e["count"], e["error_count"]) for e in entries] == [
        ("POST", "api.example.com", "/search", "429", 1, 1),
        ("GET", "api.example.com", "/v1/orders/?", "200", 2, 0),
    ]
    assert all(e["kind"] == "http" for e in entries)
    assert calls["hosts"] == ["api.example.com"]
    assert sorted(calls["targets"]) == ["/search", "/v1/orders/?"]
    assert sorted(calls["statuses"]) == ["200", "429"]
    assert sorted(calls["endpoints"]) == ["GET api.example.com/v1/orders/?", "POST api.example.com/search"]
    assert calls["total_count"] == 3
    assert calls["total_error_count"] == 1
    assert calls["total_duration_ms"] >= 0
    assert "omitted_count" not in calls
    assert "secret" not in str(calls) and "pw" not in str(calls)


def test_ids_are_templated(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        _http_call(tracer, "http://svc:8080/v1/customers/9f1c2e3a-1b2c-4d5e-8f90-aabbccddeeff/items/0123456789abcdef0")

    (entry,) = _downstream_calls(_event(llmobs_events, "t"))["calls"]
    assert entry["host"] == "svc:8080"
    assert entry["target"] == "/v1/customers/?/items/?"


def test_nested_client_span_is_counted_once(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        with tracer.trace("requests.request", span_type="http") as outer:
            outer.set_tag(SPAN_KIND, SpanKind.CLIENT)
            outer.set_tag("http.url", "https://api.example.com/v1/orders/1")
            outer.set_tag("http.method", "GET")
            outer.set_tag("http.status_code", "200")
            _http_call(tracer, "https://api.example.com/v1/orders/1", component="urllib3")

    (entry,) = _downstream_calls(_event(llmobs_events, "t"))["calls"]
    assert entry["count"] == 1


def test_db_and_grpc_calls(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        with tracer.trace("postgres.query", span_type="sql") as span:
            span.set_tag(SPAN_KIND, SpanKind.CLIENT)
            span.set_tag("db.system", "postgresql")
            span.set_tag("out.host", "db.internal")
            span.resource = "SELECT * FROM users WHERE email = 'a@b.c'"
        with tracer.trace("grpc", span_type="grpc") as span:
            span.set_tag(SPAN_KIND, SpanKind.CLIENT)
            span.set_tag("grpc.method.path", "/search.Search/Query")

    calls = _downstream_calls(_event(llmobs_events, "t"))
    entries = {e["kind"]: e for e in calls["calls"]}
    assert "SELECT" not in str(calls)
    assert entries["db"]["method"] == "postgresql"
    assert entries["db"]["host"] == "db.internal"
    assert entries["db"]["target"] == "postgres.query"
    assert entries["grpc"]["target"] == "/search.Search/Query"


def test_non_client_spans_are_ignored(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        with tracer.trace("internal.work"):
            pass
        with tracer.trace("web.request", span_type="web") as span:
            span.set_tag(SPAN_KIND, SpanKind.SERVER)
            span.set_tag("http.url", "https://me.example.com/")

    assert _downstream_calls(_event(llmobs_events, "t")) is None


def test_only_tool_spans_report_calls(llmobs, llmobs_events, tracer):
    with llmobs.workflow("w"):
        _http_call(tracer, "https://api.example.com/a")
        with llmobs.tool("t"):
            with llmobs.llm("model", model_provider="openai", name="llm"):
                _http_call(tracer, "https://api.openai.com/v1/responses", method="POST")

    assert _downstream_calls(_event(llmobs_events, "w")) is None
    assert _downstream_calls(_event(llmobs_events, "llm")) is None
    assert _downstream_calls(_event(llmobs_events, "t")) is None


def test_kind_set_after_calls_finish(llmobs, llmobs_events, tracer):
    """Integrations such as MCP only set the span kind once the tool handler has returned."""
    with tracer.trace("mcp.tools/call", span_type=SpanTypes.LLM) as span:
        _http_call(tracer, "https://api.example.com/v1/orders/1")
        _annotate_llmobs_span_data(span, kind="tool", name="mcp_tool")

    (entry,) = _downstream_calls(_event(llmobs_events, "mcp_tool"))["calls"]
    assert entry["target"] == "/v1/orders/?"


def test_asyncio_gather(llmobs, llmobs_events, tracer):
    async def call(i):
        await asyncio.sleep(0)
        _http_call(tracer, "https://api.example.com/v1/orders/%d" % i)

    async def run():
        with llmobs.tool("t"):
            await asyncio.gather(*(call(i) for i in range(3)))

    asyncio.run(run())
    (entry,) = _downstream_calls(_event(llmobs_events, "t"))["calls"]
    assert entry["count"] == 3


def test_thread_pool_calls_are_attributed(llmobs, llmobs_events, tracer):
    patch_futures()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with llmobs.tool("t"):
                pool.submit(_http_call, tracer, "https://api.example.com/v1/pool/1").result()
    finally:
        unpatch_futures()

    (entry,) = _downstream_calls(_event(llmobs_events, "t"))["calls"]
    assert entry["target"] == "/v1/pool/?"


def test_calls_after_tool_finishes_are_dropped(llmobs, llmobs_events, tracer):
    with llmobs.tool("t") as tool:
        pass
    with tracer.start_span("late", child_of=tool, activate=True) as late:
        late.set_tag(SPAN_KIND, SpanKind.CLIENT)
        late.set_tag("http.url", "https://api.example.com/late")

    assert _downstream_calls(_event(llmobs_events, "t")) is None


def test_distinct_endpoints_are_capped(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        for i in range(MAX_DOWNSTREAM_CALLS + 5):
            _http_call(tracer, "https://api.example.com/path-%d" % i)

    calls = _downstream_calls(_event(llmobs_events, "t"))
    assert len(calls["calls"]) == MAX_DOWNSTREAM_CALLS
    assert calls["omitted_count"] == 5
    assert calls["total_count"] == MAX_DOWNSTREAM_CALLS + 5


def test_concurrent_tools_do_not_mix(llmobs, llmobs_events, tracer):
    async def tool(name, path):
        with llmobs.tool(name):
            await asyncio.sleep(0)
            _http_call(tracer, "https://api.example.com/%s" % path)
            await asyncio.sleep(0)

    async def run():
        await asyncio.gather(tool("a", "alpha"), tool("b", "beta"))

    asyncio.run(run())
    assert [e["target"] for e in _downstream_calls(_event(llmobs_events, "a"))["calls"]] == ["/alpha"]
    assert [e["target"] for e in _downstream_calls(_event(llmobs_events, "b"))["calls"]] == ["/beta"]


def _client_span(tracer, name, **tags):
    with tracer.trace(name) as span:
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        for key, value in tags.items():
            span.set_tag(key, value)
    return span


def test_client_nested_through_internal_span_is_counted_once(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        with tracer.trace("sdk.call") as outer:
            outer.set_tag(SPAN_KIND, SpanKind.CLIENT)
            outer.set_tag("http.url", "https://api.example.com/v1/orders/1")
            with tracer.trace("retry.attempt"):
                _http_call(tracer, "https://api.example.com/v1/orders/1", component="urllib3")

    assert _downstream_calls(_event(llmobs_events, "t"))["total_count"] == 1


def test_otel_semantic_http_attributes(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        _client_span(
            tracer,
            "http.request",
            **{
                "url.full": "https://api.example.com/v1/orders/7",
                "http.request.method": "GET",
                "http.response.status_code": "503",
            },
        )

    (entry,) = _downstream_calls(_event(llmobs_events, "t"))["calls"]
    assert (entry["kind"], entry["method"], entry["host"], entry["target"], entry["status"]) == (
        "http",
        "GET",
        "api.example.com",
        "/v1/orders/?",
        "503",
    )
    assert entry["error_count"] == 1


def test_db_ports_and_endpoint_labels(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        for port in ("5432", "5433"):
            _client_span(
                tracer,
                "postgres.query",
                **{"db.system": "postgresql", "out.host": "db.internal", "network.destination.port": port},
            )
        _client_span(
            tracer,
            "grpc",
            **{
                "grpc.method.path": "/search.Search/Query",
                "out.host": "search",
                "grpc.status.code": "StatusCode.UNAVAILABLE",
            },
        )

    calls = _downstream_calls(_event(llmobs_events, "t"))
    assert sorted(calls["endpoints"]) == [
        "postgresql db.internal:5432 postgres.query",
        "postgresql db.internal:5433 postgres.query",
        "search/search.Search/Query",
    ]
    assert calls["statuses"] == ["StatusCode.UNAVAILABLE"]
    assert calls["total_error_count"] == 1


def test_malformed_and_ipv6_urls(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        _http_call(tracer, "http://[::1/broken")
        _http_call(tracer, "http://[::1]:80/x")
        _http_call(tracer, "http://[::1:80]/x")

    calls = _downstream_calls(_event(llmobs_events, "t"))
    assert calls["total_count"] == 3
    assert {"[::1]:80", "[::1:80]"} <= set(calls["hosts"])


def test_secret_like_and_long_paths_are_redacted(llmobs, llmobs_events, tracer):
    with llmobs.tool("t"):
        _http_call(
            tracer, "https://hooks.example.com/services/T0A1B2C3D/B4E5F6G7H/aBcDeFgHiJkLmNoPqRsT1234", method="POST"
        )
        _http_call(tracer, "https://api.example.com/users/jane@example.com/profile")
        _http_call(tracer, "https://api.example.com/" + "a" * 1000)

    calls = _downstream_calls(_event(llmobs_events, "t"))
    assert "aBcDeFgHiJkLmNoPqRsT1234" not in str(calls)
    assert "jane@example.com" not in str(calls)
    assert "/users/?/profile" in calls["targets"]
    assert all(len(target) <= 256 for target in calls["targets"])
