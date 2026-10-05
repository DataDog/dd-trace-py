import copy
import json

import pytest

from tests import otel_semantics_snapshot
from tests.otel_semantics_snapshot import assert_matches_snapshot
from tests.otel_semantics_snapshot import assert_otel_semantics_snapshot
from tests.otel_semantics_snapshot import normalize_otlp_requests
from tests.otel_semantics_snapshot import otel_semantics_env
from tests.otel_semantics_snapshot import otlp_base_url


def _attribute(key, value):
    return {"key": key, "value": {"string_value": value}}


def _span(name, trace_id, span_id, start, end, parent_span_id=None, attributes=()):
    span = {
        "trace_id": trace_id,
        "span_id": span_id,
        "name": name,
        "kind": "SPAN_KIND_SERVER",
        "start_time_unix_nano": str(start),
        "end_time_unix_nano": str(end),
        "trace_state": "ot=rv:abcdef",
        "attributes": list(attributes),
    }
    if parent_span_id:
        span["parent_span_id"] = parent_span_id
    return span


def _request(spans, resource_attributes=(), scope_version="1.2.3"):
    return {
        "resource_spans": [
            {
                "resource": {"attributes": list(resource_attributes)},
                "scope_spans": [{"scope": {"name": "ddtrace", "version": scope_version}, "spans": spans}],
            }
        ]
    }


def _payload(trace_id, root_id, child_id, base_time, version):
    return [
        _request(
            [
                _span(
                    "GET /",
                    trace_id,
                    root_id,
                    base_time,
                    base_time + 10,
                    attributes=[_attribute("url.path", "/"), _attribute("http.request.method", "GET")],
                ),
                _span("child", trace_id, child_id, base_time + 1, base_time + 5, parent_span_id=root_id),
            ],
            resource_attributes=[
                _attribute("service.name", "svc"),
                _attribute("telemetry.sdk.version", version),
                _attribute("process.pid", str(base_time)),
            ],
            scope_version=version,
        )
    ]


def test_normalize_is_stable_across_runs():
    first = _payload("dHJhY2UtYQ==", "c3Bhbi1h", "c3Bhbi1i", 1_000, "1.0.0")
    second = _payload("dHJhY2UtYg==", "c3Bhbi14", "c3Bhbi15", 9_999_999, "2.0.0")

    assert normalize_otlp_requests(first) == normalize_otlp_requests(second)


def test_normalize_preserves_parent_child_links():
    normalized = normalize_otlp_requests(_payload("dHJhY2U=", "cm9vdA==", "Y2hpbGQ=", 100, "1.0.0"))

    spans = normalized["resource_spans"][0]["scope_spans"][0]["spans"]
    by_name = {span["name"]: span for span in spans}
    assert by_name["GET /"]["span_id"] == "span_1"
    assert "parent_span_id" not in by_name["GET /"]
    assert by_name["child"]["parent_span_id"] == by_name["GET /"]["span_id"]
    assert {span["trace_id"] for span in spans} == {"trace_1"}


def test_normalize_keeps_the_otlp_shape_and_typed_values():
    requests = _payload("dHJhY2U=", "cm9vdA==", "Y2hpbGQ=", 100, "1.0.0")
    requests[0]["resource_spans"][0]["scope_spans"][0]["spans"][0]["attributes"].append(
        {"key": "http.response.status_code", "value": {"int_value": "200"}}
    )

    normalized = normalize_otlp_requests(requests)

    span = normalized["resource_spans"][0]["scope_spans"][0]["spans"][0]
    assert span["kind"] == "SPAN_KIND_SERVER"
    assert {"key": "http.response.status_code", "value": {"int_value": "200"}} in span["attributes"]
    assert {"key": "service.name", "value": {"string_value": "svc"}} in normalized["resource_spans"][0]["resource"][
        "attributes"
    ]


def test_normalize_replaces_timestamps_and_does_not_mutate_input():
    requests = _payload("dHJhY2U=", "cm9vdA==", "Y2hpbGQ=", 100, "1.0.0")
    original = copy.deepcopy(requests)

    normalized = normalize_otlp_requests(requests)

    span = normalized["resource_spans"][0]["scope_spans"][0]["spans"][0]
    assert span["start_time_unix_nano"] == "<start_time_unix_nano>"
    assert span["end_time_unix_nano"] == "<end_time_unix_nano>"
    assert requests == original


def test_normalize_sorts_attributes_and_drops_ignored_ones():
    requests = [
        _request(
            [
                _span(
                    "s",
                    "dA==",
                    "cw==",
                    1,
                    2,
                    attributes=[_attribute("b", "2"), _attribute("a", "1"), _attribute("x", "9"), _attribute("y", "8")],
                )
            ],
            resource_attributes=[_attribute("process.pid", "42"), _attribute("service.name", "svc")],
        )
    ]

    normalized = normalize_otlp_requests(requests, ignores=["x", "meta.y"])

    resource = normalized["resource_spans"][0]["resource"]
    assert [a["key"] for a in resource["attributes"]] == ["service.name"]
    span = normalized["resource_spans"][0]["scope_spans"][0]["spans"][0]
    assert [a["key"] for a in span["attributes"]] == ["a", "b"]


def test_normalize_ignores_drop_attributes_but_not_fields_with_the_same_name():
    requests = [
        _request([_span("s", "dA==", "cw==", 1, 2, attributes=[_attribute("name", "x"), _attribute("a", "1")])])
    ]

    normalized = normalize_otlp_requests(requests, ignores=["name", "kind"])

    span = normalized["resource_spans"][0]["scope_spans"][0]["spans"][0]
    assert span["name"] == "s"
    assert span["kind"] == "SPAN_KIND_SERVER"
    assert [a["key"] for a in span["attributes"]] == ["a"]


def test_normalize_drops_random_fields_by_default():
    normalized = normalize_otlp_requests(_payload("dHJhY2U=", "cm9vdA==", "Y2hpbGQ=", 100, "1.0.0"))

    scope_spans = normalized["resource_spans"][0]["scope_spans"][0]
    assert "version" not in scope_spans["scope"]
    assert all("trace_state" not in span for span in scope_spans["spans"])


def test_normalize_replaces_event_timestamps():
    span = _span("s", "dA==", "cw==", 1, 2)
    span["events"] = [{"name": "exception", "time_unix_nano": "12345", "attributes": []}]

    normalized = normalize_otlp_requests([_request([span])])

    event = normalized["resource_spans"][0]["scope_spans"][0]["spans"][0]["events"][0]
    assert event["time_unix_nano"] == "<time_unix_nano>"


def test_normalize_rewrites_link_ids_with_the_span_mappings():
    root = _span("root", "dA==", "cm9vdA==", 1, 3)
    child = _span("child", "dA==", "Y2hpbGQ=", 2, 3, parent_span_id="cm9vdA==")
    child["links"] = [
        {"trace_id": "dA==", "span_id": "cm9vdA=="},
        {"trace_id": "b3RoZXI=", "span_id": "ZXh0"},
    ]

    normalized = normalize_otlp_requests([_request([root, child])])

    spans = normalized["resource_spans"][0]["scope_spans"][0]["spans"]
    assert spans[1]["links"] == [
        {"trace_id": "trace_1", "span_id": "span_1"},
        {"trace_id": "trace_2", "span_id": "span_3"},
    ]


def test_normalize_ignores_start_order_of_concurrent_traces_and_siblings():
    def payload(first, second):
        return [
            _request(
                [
                    _span("a", "dEE=", "YQ==", first, 10),
                    _span("b", "dEI=", "Yg==", second, 10),
                    _span("root", "dEM=", "cm9vdA==", 1, 10),
                    _span("left", "dEM=", "bA==", first, 10, parent_span_id="cm9vdA=="),
                    _span("right", "dEM=", "cg==", second, 10, parent_span_id="cm9vdA=="),
                ]
            )
        ]

    assert normalize_otlp_requests(payload(2, 3)) == normalize_otlp_requests(payload(3, 2))


def test_normalize_rejects_invalid_time_range():
    requests = [_request([_span("bad", "dA==", "cw==", 10, 5)])]

    with pytest.raises(AssertionError, match="invalid time range"):
        normalize_otlp_requests(requests)


def test_snapshot_is_generated_when_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    snapshot_file = tmp_path / "nested" / "token.json"
    normalized = {"resource_spans": [{"scope_spans": []}]}

    assert_matches_snapshot(normalized, snapshot_file)

    assert json.loads(snapshot_file.read_text()) == normalized


def test_snapshot_matches_then_fails_on_difference(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    snapshot_file = tmp_path / "token.json"
    assert_matches_snapshot({"resource_spans": [{"a": 1}]}, snapshot_file)

    assert_matches_snapshot({"resource_spans": [{"a": 1}]}, snapshot_file)
    with pytest.raises(AssertionError, match="snapshot mismatch"):
        assert_matches_snapshot({"resource_spans": [{"a": 2}]}, snapshot_file)


def test_missing_snapshot_fails_in_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "true")

    with pytest.raises(AssertionError, match="not found"):
        assert_matches_snapshot({"resource_spans": []}, tmp_path / "token.json")

    assert not (tmp_path / "token.json").exists()


def test_assert_otel_semantics_snapshot_writes_the_token_named_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    requests = _payload("dHJhY2U=", "cm9vdA==", "Y2hpbGQ=", 100, "1.0.0")
    monkeypatch.setattr(
        otel_semantics_snapshot, "fetch_otlp_requests", lambda token, timeout, min_traces=None: requests
    )

    assert_otel_semantics_snapshot("my.token", snapshot_dir=tmp_path)

    assert (tmp_path / "my.token.json").exists()


def test_assert_otel_semantics_snapshot_fails_when_no_spans_were_received(tmp_path, monkeypatch):
    monkeypatch.setattr(otel_semantics_snapshot, "fetch_otlp_requests", lambda token, timeout, min_traces=None: [])

    with pytest.raises(AssertionError, match="no OTLP spans received"):
        assert_otel_semantics_snapshot("my.token", snapshot_dir=tmp_path)


def test_assert_otel_semantics_snapshot_waits_for_the_requested_traces(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    calls = []

    def fetch(token, timeout, min_traces=None):
        calls.append(min_traces)
        return _payload("dHJhY2U=", "cm9vdA==", "Y2hpbGQ=", 100, "1.0.0")

    monkeypatch.setattr(otel_semantics_snapshot, "fetch_otlp_requests", fetch)

    assert_otel_semantics_snapshot("my.token", snapshot_dir=tmp_path, wait_for_num_traces=2)

    assert calls == [2]


def test_assert_otel_semantics_snapshot_accepts_no_spans_when_zero_traces_are_expected(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(otel_semantics_snapshot, "fetch_otlp_requests", lambda token, timeout, min_traces=None: [])

    assert_otel_semantics_snapshot("my.token", snapshot_dir=tmp_path, wait_for_num_traces=0)

    assert (tmp_path / "my.token.json").exists()


def test_otlp_base_url_resolution(monkeypatch):
    monkeypatch.delenv("DD_TEST_OTLP_URL", raising=False)
    monkeypatch.delenv("DD_TRACE_AGENT_URL", raising=False)
    assert otlp_base_url() == "http://localhost:4318"

    monkeypatch.setenv("DD_TRACE_AGENT_URL", "http://testagent:9126")
    assert otlp_base_url() == "http://testagent:4318"

    monkeypatch.setenv("DD_TEST_OTLP_URL", "http://localhost:32768/")
    assert otlp_base_url() == "http://localhost:32768"


def test_otel_semantics_env_enables_semantics_and_sends_the_session_token_unchanged(monkeypatch):
    monkeypatch.setenv("DD_TEST_OTLP_URL", "http://localhost:4318")

    env = otel_semantics_env("tests.contrib.requests.test_requests.test_x[302]")

    assert env["DD_TRACE_OTEL_SEMANTICS_ENABLED"] == "true"
    assert env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == "http://localhost:4318/v1/traces"
    assert env["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] == (
        f"{otel_semantics_snapshot.SESSION_TOKEN_HEADER}=tests.contrib.requests.test_requests.test_x[302]"
    )


def test_otel_semantics_env_rejects_a_token_with_a_comma():
    with pytest.raises(ValueError, match="comma"):
        otel_semantics_env("tests.test_x[a, b]")
