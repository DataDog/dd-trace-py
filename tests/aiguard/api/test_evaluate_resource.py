"""AIGuardClient._evaluate_resource: the runtime policy evaluation of one target, such as an MCP server connection."""

import logging
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace import config
from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import AIGuardClientError
from ddtrace.aiguard import Options
from ddtrace.aiguard import middleware
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard.middleware import AIGuardMiddleware
from ddtrace.aiguard.middleware import register_middleware
from ddtrace.constants import USER_KEEP
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpClientError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import InvalidConfigError
from ddtrace.internal.native import TimedOutError
from tests.aiguard.utils import find_ai_guard_span
from tests.aiguard.utils import mock_evaluate_resource_response


EXECUTE_REQUEST = "ddtrace.aiguard._api_client.AIGuardClient._execute_request"
ADD_COUNT_METRIC = "ddtrace.internal.telemetry.telemetry_writer.add_count_metric"
TARGET = {
    "type": "mcp_server",
    "mcp_server": {"operation": "connect", "name": "atlassian", "url": "https://mcp.example.com", "transport": "sse"},
}
AUTO = (("source", AI_GUARD.SOURCE_AUTO), ("integration", AI_GUARD.INTEGRATION_OPENAI_AGENTS))
SDK = (("source", AI_GUARD.SOURCE_SDK), ("integration", AI_GUARD.INTEGRATION_NONE))
META = {"service": config.service, "env": config.env}


def _response(status=200, body=None, json_error=None):
    response = Mock()
    response.status = status
    if json_error is not None:
        response.get_json.side_effect = json_error
    else:
        response.get_json.return_value = body
    return response


def _results(*results):
    return _response(body={"data": {"attributes": {"results": list(results)}}})


def _metrics(add_count_metric, metric):
    return [args[3] for args, _ in add_count_metric.call_args_list if args[1] == metric]


def _evaluate_auto(client):
    return client._evaluate_resource(
        TARGET, Options(block=True), source=AI_GUARD.SOURCE_AUTO, integration=AI_GUARD.INTEGRATION_OPENAI_AGENTS
    )


@patch(EXECUTE_REQUEST)
def test_request_is_the_rfc_payload_sent_to_the_resource_endpoint(mock_execute_request, ai_guard_client):
    mock_execute_request.return_value = mock_evaluate_resource_response("ALLOW")

    ai_guard_client._evaluate_resource(TARGET)

    # One target per request, the default profile and the meta of evaluate; never the /evaluate endpoint.
    mock_execute_request.assert_called_once_with(
        "https://api.example.com/ai-guard/evaluate/resource",
        {"data": {"attributes": {"profile": "default", "targets": [TARGET], "meta": META}}},
    )


@pytest.mark.parametrize(
    "results,expected",
    [
        pytest.param([{"target_index": 0, "action": "DENY"}], "DENY", id="target_index 0"),
        pytest.param([{"action": "DENY"}], "DENY", id="list position without target_index"),
        pytest.param(
            [{"target_index": 1, "action": "ALLOW"}, {"target_index": 0, "action": "DENY"}],
            "DENY",
            id="target_index wins over the list position",
        ),
        pytest.param(
            ["not a result", {"target_index": 0, "action": "ABORT"}], "ABORT", id="entries that are not objects"
        ),
    ],
)
@patch(EXECUTE_REQUEST)
def test_result_is_selected_by_target_index(mock_execute_request, ai_guard_client, results, expected):
    mock_execute_request.return_value = _results(*results)

    evaluation = ai_guard_client._evaluate_resource(TARGET, Options(block=False))

    assert evaluation["action"] == expected


@pytest.mark.parametrize(
    "action,remote_block,local_block,blocked",
    [
        ("ALLOW", True, True, False),
        ("DENY", True, True, True),
        ("ABORT", True, True, True),
        ("DENY", False, True, False),
        ("ABORT", True, False, False),
    ],
)
@patch(ADD_COUNT_METRIC)
@patch(EXECUTE_REQUEST)
def test_block_needs_the_remote_and_the_local_settings(
    mock_execute_request, add_count_metric, ai_guard_client, test_spans, action, remote_block, local_block, blocked
):
    mock_execute_request.return_value = mock_evaluate_resource_response(action, reason="By policy", block=remote_block)

    if blocked:
        with pytest.raises(AIGuardAbortError) as exc_info:
            ai_guard_client._evaluate_resource(TARGET, Options(block=local_block))
        assert (exc_info.value.action, exc_info.value.reason) == (action, "By policy")
    else:
        evaluation = ai_guard_client._evaluate_resource(TARGET, Options(block=local_block))
        assert evaluation == {"action": action, "reason": "By policy"}

    span = find_ai_guard_span(test_spans)
    assert span.get_tag(AI_GUARD.ACTION_TAG) == action
    assert span.get_tag(AI_GUARD.BLOCKED_TAG) == ("true" if blocked else None)
    block_tag = "true" if blocked else "false"
    # Like evaluate, without the redacted tag: a resource evaluation never redacts.
    assert _metrics(add_count_metric, AI_GUARD.REQUESTS_METRIC) == [
        (("action", action), ("block", block_tag), ("error", "false"), ("skipped", "false")) + SDK
    ]
    assert _metrics(add_count_metric, AI_GUARD.ERROR_METRIC) == []


@patch(EXECUTE_REQUEST)
def test_missing_remote_blocking_setting_does_not_block(mock_execute_request, ai_guard_client):
    mock_execute_request.return_value = _results({"target_index": 0, "action": "DENY"})

    assert ai_guard_client._evaluate_resource(TARGET)["action"] == "DENY"


@patch(EXECUTE_REQUEST)
def test_span_identifies_the_connection_evaluation(mock_execute_request, ai_guard_client, tracer, test_spans):
    target = {"type": "mcp_server", "mcp_server": {"operation": "connect", "transport": "stdio", "command": "uvx"}}
    mock_execute_request.return_value = mock_evaluate_resource_response(
        "DENY", reason="MCP server denied by policy", policy={"id": "policy_123", "version": "42"}
    )

    with tracer.trace("agent"):
        with pytest.raises(AIGuardAbortError):
            ai_guard_client._evaluate_resource(
                target, Options(block=True), source=AI_GUARD.SOURCE_AUTO, integration=AI_GUARD.INTEGRATION_OPENAI_AGENTS
            )
    target["mcp_server"]["command"] = "changed after the evaluation"

    (span,) = [span for span in test_spans.spans if span.name == AI_GUARD.RESOURCE_TYPE]
    assert {key: value for key, value in span.get_tags().items() if key.startswith("ai_guard.")} == {
        AI_GUARD.TARGET_TAG: "mcp_server",
        AI_GUARD.SOURCE_TAG: "auto",
        AI_GUARD.INTEGRATION_TAG: "openai_agents",
        AI_GUARD.ACTION_TAG: "DENY",
        AI_GUARD.REASON_TAG: "MCP server denied by policy",
        AI_GUARD.BLOCKED_TAG: "true",
        AI_GUARD.POLICY_ID_TAG: "policy_123",
        AI_GUARD.POLICY_VERSION_TAG: "42",
    }
    # The meta-struct reports the target as sent, not as changed later.
    assert span._get_struct_tag(AI_GUARD.STRUCT) == {
        "targets": [
            {"type": "mcp_server", "mcp_server": {"operation": "connect", "transport": "stdio", "command": "uvx"}}
        ]
    }


@pytest.mark.parametrize(
    "policy,expected",
    [
        pytest.param({"id": "policy_123", "version": "42"}, {"id": "policy_123", "version": "42"}, id="strings"),
        pytest.param({"id": 123, "version": 7}, {"id": "123", "version": "7"}, id="integers"),
        pytest.param({"id": "policy_123"}, {"id": "policy_123"}, id="id only"),
        pytest.param({"id": True, "version": None, "extra": "x"}, None, id="other types"),
        pytest.param({"id": "", "version": ""}, None, id="empty strings"),
        pytest.param({"id": 0, "version": ""}, {"id": "0"}, id="integer zero"),
        pytest.param({"id": {"nested": 1}, "version": 4.2}, None, id="objects and floats"),
        pytest.param("policy_123", None, id="not an object"),
        pytest.param(None, None, id="absent"),
    ],
)
@patch(EXECUTE_REQUEST)
def test_policy_is_reported_when_the_service_identifies_it(
    mock_execute_request, ai_guard_client, test_spans, policy, expected
):
    mock_execute_request.return_value = _results({"target_index": 0, "action": "ALLOW", "policy": policy})

    evaluation = ai_guard_client._evaluate_resource(TARGET)

    assert evaluation.get("policy") == expected
    span = find_ai_guard_span(test_spans)
    assert span.get_tag(AI_GUARD.POLICY_ID_TAG) == (expected or {}).get("id")
    assert span.get_tag(AI_GUARD.POLICY_VERSION_TAG) == (expected or {}).get("version")


@pytest.mark.parametrize(
    "transport_error,error_type",
    [
        (ConnectionFailedError("refused"), AI_GUARD.ERROR_CONNECTION),
        (TimedOutError("too slow"), AI_GUARD.ERROR_TIMEOUT),
        (InvalidConfigError("bad url"), AI_GUARD.ERROR_INVALID_CONFIG),
        (HttpIoError("broken pipe"), AI_GUARD.ERROR_NETWORK),
        (HttpClientError("something else"), AI_GUARD.ERROR_CLIENT),
        (ValueError("bug in our code"), AI_GUARD.ERROR_INTERNAL),
    ],
)
@patch(ADD_COUNT_METRIC)
@patch(EXECUTE_REQUEST)
def test_transport_failures_are_classified_as_in_evaluate(
    mock_execute_request, add_count_metric, ai_guard_client, test_spans, transport_error, error_type
):
    mock_execute_request.side_effect = transport_error

    with pytest.raises(AIGuardClientError) as exc_info:
        _evaluate_auto(ai_guard_client)

    # Never chained, as in evaluate: the transport failure can quote the endpoint.
    assert exc_info.value.__cause__ is None and exc_info.value.__context__ is None
    assert _metrics(add_count_metric, AI_GUARD.REQUESTS_METRIC) == [(("error", "true"), ("skipped", "false")) + AUTO]
    assert _metrics(add_count_metric, AI_GUARD.ERROR_METRIC) == [(("type", error_type),) + AUTO]
    _assert_failed_span(test_spans)


@pytest.mark.parametrize(
    "response,error_type,http_status",
    [
        pytest.param(_response(500, {"errors": [{"title": "boom"}]}), AI_GUARD.ERROR_BAD_STATUS, "500", id="500"),
        pytest.param(_response(503, None), AI_GUARD.ERROR_BAD_STATUS, "503", id="503 without body"),
        pytest.param(_response(418, {}), AI_GUARD.ERROR_BAD_STATUS, AI_GUARD.STATUS_OTHER, id="undeclared status"),
        pytest.param(
            _response(502, json_error=ValueError("not json")), AI_GUARD.ERROR_BAD_STATUS, "502", id="502 not json"
        ),
        pytest.param(
            _response(200, json_error=ValueError("not json")), AI_GUARD.ERROR_BAD_RESPONSE, None, id="not json"
        ),
        pytest.param(_response(200, {"unexpected": "body"}), AI_GUARD.ERROR_BAD_RESPONSE, None, id="no results"),
        pytest.param(_response(200, ["not", "an", "object"]), AI_GUARD.ERROR_BAD_RESPONSE, None, id="body list"),
        pytest.param(
            _response(200, {"data": {"attributes": {"results": {"action": "ALLOW"}}}}),
            AI_GUARD.ERROR_BAD_RESPONSE,
            None,
            id="results not a list",
        ),
        pytest.param(_results(), AI_GUARD.ERROR_BAD_RESPONSE, None, id="empty results"),
        pytest.param(
            _results({"target_index": 1, "action": "ALLOW"}), AI_GUARD.ERROR_BAD_RESPONSE, None, id="other target"
        ),
        pytest.param(
            _results({"target_index": 0, "action": "GO_TO_SLEEP"}), AI_GUARD.ERROR_BAD_RESPONSE, None, id="bad action"
        ),
        pytest.param(_results({"target_index": 0}), AI_GUARD.ERROR_BAD_RESPONSE, None, id="no action"),
        # One target gets one result: an ambiguous answer is a failure, not the first match.
        pytest.param(
            _results({"action": "ALLOW"}, {"target_index": 0, "action": "DENY"}),
            AI_GUARD.ERROR_BAD_RESPONSE,
            None,
            id="list position and target_index both match",
        ),
        pytest.param(
            _results({"action": "ALLOW"}, {"action": "DENY"}),
            AI_GUARD.ERROR_BAD_RESPONSE,
            None,
            id="two results without target_index",
        ),
        pytest.param(
            _results({"target_index": 0, "action": "ALLOW"}, {"target_index": 0, "action": "DENY"}),
            AI_GUARD.ERROR_BAD_RESPONSE,
            None,
            id="two results with target_index 0",
        ),
        pytest.param(
            _results({"target_index": False, "action": "DENY"}), AI_GUARD.ERROR_BAD_RESPONSE, None, id="bool index"
        ),
        pytest.param(
            _results({"target_index": 0.0, "action": "DENY"}), AI_GUARD.ERROR_BAD_RESPONSE, None, id="float index"
        ),
        pytest.param(
            _results({"target_index": None, "action": "DENY"}), AI_GUARD.ERROR_BAD_RESPONSE, None, id="null index"
        ),
    ],
)
@patch(ADD_COUNT_METRIC)
@patch(EXECUTE_REQUEST)
def test_failed_evaluations_are_never_reported_as_allow(
    mock_execute_request, add_count_metric, ai_guard_client, test_spans, response, error_type, http_status
):
    mock_execute_request.return_value = response

    with pytest.raises(AIGuardClientError):
        _evaluate_auto(ai_guard_client)

    assert _metrics(add_count_metric, AI_GUARD.REQUESTS_METRIC) == [(("error", "true"), ("skipped", "false")) + AUTO]
    extra = (("http_status", http_status),) if http_status else ()
    assert _metrics(add_count_metric, AI_GUARD.ERROR_METRIC) == [(("type", error_type),) + AUTO + extra]
    _assert_failed_span(test_spans)


def _assert_failed_span(test_spans):
    span = find_ai_guard_span(test_spans)
    assert span.error == 1
    assert span.get_tag(AI_GUARD.TARGET_TAG) == "mcp_server"
    for tag in (AI_GUARD.ACTION_TAG, AI_GUARD.REASON_TAG, AI_GUARD.BLOCKED_TAG):
        assert span.get_tag(tag) is None


@patch(EXECUTE_REQUEST)
def test_transport_failure_does_not_report_the_endpoint(mock_execute_request, ai_guard_client):
    mock_execute_request.side_effect = ConnectionFailedError(
        f"cannot reach {ai_guard_client._endpoint}/evaluate/resource"
    )

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client._evaluate_resource(TARGET)

    assert ai_guard_client._endpoint not in str(exc_info.value)
    assert "<endpoint>" in str(exc_info.value)


@patch(EXECUTE_REQUEST)
def test_failures_log_at_debug_only(mock_execute_request, ai_guard_client, caplog):
    mock_execute_request.side_effect = ConnectionFailedError("refused")

    with caplog.at_level("DEBUG", logger="ddtrace"):
        with pytest.raises(AIGuardClientError):
            ai_guard_client._evaluate_resource(TARGET)

    assert f"AI Guard resource evaluation failed ({AI_GUARD.ERROR_CONNECTION})" in caplog.text
    aiguard_records = [record for record in caplog.records if record.name.startswith("ddtrace.aiguard")]
    assert aiguard_records and all(record.levelno == logging.DEBUG for record in aiguard_records)


@patch(EXECUTE_REQUEST)
def test_root_span_is_kept_and_marked(mock_execute_request, ai_guard_client, tracer, test_spans):
    mock_execute_request.return_value = mock_evaluate_resource_response("ALLOW")

    with tracer.trace("agent") as root_span:
        ai_guard_client._evaluate_resource(TARGET)

    assert root_span.context.sampling_priority == USER_KEEP
    assert root_span.get_tag(AI_GUARD.EVENT_TAG) == "true"


@patch(EXECUTE_REQUEST)
def test_request_tags_reach_the_root_and_ai_guard_spans(mock_execute_request, ai_guard_client, tracer, test_spans):
    mock_execute_request.return_value = mock_evaluate_resource_response("DENY")

    with core.context_with_data("test.request"):
        with tracer.trace("web.request") as root_span:
            root_span.set_tag("http.useragent", "test-agent/1.0")
            core.set_item(AI_GUARD.CLIENT_IP_CORE_KEY, ("8.8.8.8", "10.0.0.1"))
            with pytest.raises(AIGuardAbortError):
                ai_guard_client._evaluate_resource(TARGET)
            # The stashed IPs are used once, so a later evaluation cannot inherit them.
            assert core.find_item(AI_GUARD.CLIENT_IP_CORE_KEY) is None

    assert root_span.get_tag(http.CLIENT_IP) == "8.8.8.8"
    assert root_span.get_tag(net.CLIENT_IP) == "10.0.0.1"
    (span,) = [span for span in test_spans.spans if span.name == AI_GUARD.RESOURCE_TYPE]
    assert span.get_tag(f"{AI_GUARD.TAG}.http.useragent") == "test-agent/1.0"


@pytest.mark.parametrize(
    "mcp_server,expected",
    [
        pytest.param(
            {
                "operation": "connect",
                "transport": "streamable_http",
                "name": "atlassian",
                "url": "https://user:secret@MCP.Example.com:443/mcp?token=abc#frag",
                "headers": {"Authorization": "Bearer secret"},
            },
            {
                "operation": "connect",
                "transport": "streamable_http",
                "name": "atlassian",
                "url": "https://mcp.example.com",
            },
            id="remote URL origin only, no headers",
        ),
        pytest.param(
            {
                "operation": "connect",
                "transport": "stdio",
                "command": "/usr/local/bin/npx",
                "args": ["-y", "@scope/server", "--token", "secret"],
                "env": {"API_KEY": "secret"},
                "url": "https://local.example.com",
            },
            {"operation": "connect", "transport": "stdio", "command": "npx", "args": ["@scope/server"]},
            id="stdio safe args only, no env or URL",
        ),
        pytest.param(
            {"operation": "connect", "transport": "stdio", "command": "python3.12", "args": ["my_server.main"]},
            {"operation": "connect", "transport": "stdio", "command": "python3.12", "args": ["my_server.main"]},
            id="normalized python module is kept",
        ),
        pytest.param(
            {
                "operation": "connect",
                "transport": "stdio",
                "command": "python3",
                "args": ["/home/me/server.py", "--key"],
            },
            {"operation": "connect", "transport": "stdio", "command": "python3"},
            id="python script path and other args are dropped",
        ),
        pytest.param(
            {
                "operation": "connect",
                "transport": "streamable_http",
                "name": "streamable_http: https://user:secret@mcp.example.com/mcp?token=abc",
                "url": "https://user:secret@mcp.example.com/mcp?token=abc",
            },
            {"operation": "connect", "transport": "streamable_http", "url": "https://mcp.example.com"},
            id="SDK-generated name is dropped",
        ),
        pytest.param(
            {"operation": "connect", "transport": "carrier_pigeon", "name": "local-docs"},
            {"operation": "connect", "transport": "unknown", "name": "local-docs"},
            id="unknown transport",
        ),
    ],
)
@patch(EXECUTE_REQUEST)
def test_only_the_safe_identity_is_sent_and_reported(
    mock_execute_request, ai_guard_client, test_spans, mcp_server, expected
):
    mock_execute_request.return_value = mock_evaluate_resource_response("ALLOW")

    ai_guard_client._evaluate_resource({"type": "mcp_server", "mcp_server": mcp_server})

    sent = mock_execute_request.call_args.args[1]["data"]["attributes"]["targets"]
    assert sent == [{"type": "mcp_server", "mcp_server": expected}]
    (span,) = [span for span in test_spans.spans if span.name == AI_GUARD.RESOURCE_TYPE]
    assert span._get_struct_tag(AI_GUARD.STRUCT) == {"targets": sent}


@pytest.mark.parametrize(
    "target",
    [
        pytest.param({"type": "skill", "skill": {"name": "pr-review"}}, id="unsupported type"),
        pytest.param(
            {"type": "mcp_server", "mcp_server": {"operation": "list", "transport": "stdio", "command": "uvx"}},
            id="unsupported operation",
        ),
        pytest.param(
            {"type": "mcp_server", "mcp_server": {"operation": "connect", "transport": "streamable_http"}},
            id="no identifier",
        ),
        pytest.param({"type": "mcp_server"}, id="no details"),
    ],
)
@patch(EXECUTE_REQUEST)
def test_target_without_a_safe_identity_is_not_sent(mock_execute_request, ai_guard_client, test_spans, target):
    with pytest.raises(ValueError, match="safe name, URL or command"):
        ai_guard_client._evaluate_resource(target)

    mock_execute_request.assert_not_called()
    assert [span for span in test_spans.spans if span.name == AI_GUARD.RESOURCE_TYPE] == []


@pytest.mark.parametrize("flag", ["false", "true", 1, None])
@patch(ADD_COUNT_METRIC)
@patch(EXECUTE_REQUEST)
def test_non_boolean_blocking_flag_is_a_bad_response(
    mock_execute_request, add_count_metric, ai_guard_client, test_spans, flag
):
    mock_execute_request.return_value = _results({"target_index": 0, "action": "DENY", "is_blocking_enabled": flag})

    with pytest.raises(AIGuardClientError, match="non-boolean is_blocking_enabled"):
        ai_guard_client._evaluate_resource(TARGET)

    (span,) = [span for span in test_spans.spans if span.name == AI_GUARD.RESOURCE_TYPE]
    assert span.get_tag(AI_GUARD.ACTION_TAG) is None
    assert span.get_tag(AI_GUARD.BLOCKED_TAG) is None
    error_metrics = [c.args for c in add_count_metric.call_args_list if c.args[1] == AI_GUARD.ERROR_METRIC]
    assert [dict(args[3])["type"] for args in error_metrics] == [AI_GUARD.ERROR_BAD_RESPONSE]


@patch(EXECUTE_REQUEST)
def test_without_a_tracer_nothing_is_sent(mock_execute_request, ai_guard_client):
    with patch("ddtrace.aiguard._api_client.core") as core_mock:
        core_mock.root.get_item.return_value = None
        with pytest.raises(AIGuardClientError, match="requires the global tracer"):
            ai_guard_client._evaluate_resource(TARGET)

    mock_execute_request.assert_not_called()


class _Recorder(AIGuardMiddleware):
    def __init__(self):
        self.calls = []

    def should_evaluate(self, context):
        self.calls.append("should_evaluate")
        return False

    def pre_evaluate(self, context):
        self.calls.append("pre_evaluate")

    def post_evaluate(self, context):
        self.calls.append("post_evaluate")


@patch(EXECUTE_REQUEST)
def test_middlewares_do_not_run_for_resource_evaluations(mock_execute_request, ai_guard_client):
    """The middleware contract is about messages, so a resource evaluation does not call the hooks."""
    mock_execute_request.return_value = mock_evaluate_resource_response("DENY", block=True)
    recorder = _Recorder()
    register_middleware(recorder)
    try:
        with pytest.raises(AIGuardAbortError):
            ai_guard_client._evaluate_resource(TARGET)
    finally:
        middleware._MIDDLEWARES.remove(recorder)

    assert recorder.calls == []
    mock_execute_request.assert_called_once()
