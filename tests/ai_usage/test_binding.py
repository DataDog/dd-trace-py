"""The binding's own behavior. What the library reads from each provider shape is tested in libdatadog."""

import json

import pytest

from ddtrace.internal.ai_usage import ai_usage
from ddtrace.internal.ai_usage import parse
from ddtrace.internal.ai_usage import parse_stream


pytestmark = pytest.mark.skipif(ai_usage is None, reason="native ai_usage module not built")

ATTEMPT = "gen_ai.client.provider_attempt@0.1.0"
BREAKDOWN = "trajectory.gen_ai.client.token_breakdown@0.1.0"  # nosec B105: a profile id, not a secret
OBSERVATION = {
    "operation_name": "chat",
    "provider_name": "openai",
    "duration_seconds": 1.0,
    "streaming": False,
    "input_tokens": 3,
    "output_tokens": 1,
    "observation_point": "gateway",
}
CHAT = {"model": "gpt-4o-mini", "usage": {"prompt_tokens": 3, "completion_tokens": 1}}


def lines(metrics):
    datagrams, dropped = metrics.take_dogstatsd(8192)
    assert dropped == 0
    return sorted(line for datagram in datagrams.decode().split("\0") if datagram for line in datagram.split("\n"))


def test_parse_takes_a_decoded_response_or_a_body():
    response, outcome, error = parse("auto", CHAT)
    assert response["usage"]["input_tokens"] == 3
    assert response["response_model"] == "gpt-4o-mini"
    assert (outcome, error) == ("open", None)
    assert parse("auto", json.dumps(CHAT))[0]["usage"] == response["usage"]
    assert parse("auto", json.dumps(CHAT).encode())[0]["usage"] == response["usage"]
    assert parse("auto", {})[0]["usage"] is None
    with pytest.raises(ValueError):
        parse("not_a_format", CHAT)


def test_parse_stream_reads_lines_in_order_and_skips_other_items():
    events = ["data: " + json.dumps(dict(CHAT, choices=[])), 3, None, b"data: [DONE]"]
    response, outcome, _ = parse_stream("openai_chat", events)
    assert response["usage"]["output_tokens"] == 1
    assert outcome == "completed"


def test_error_type_falls_back_only_for_a_known_failure():
    assert ai_usage.error_type("RateLimitError") == "rate_limit_error"
    assert ai_usage.error_type(None, 503) == ai_usage.error_type("", 503)
    assert ai_usage.error_type() is None
    assert ai_usage.error_type(failed=True) == "api_error"


def test_provider_ids_read_only_the_headers_asked_for():
    asked = []

    def header(name):
        asked.append(name)
        return "org-1" if name == "anthropic-organization-id" else None

    assert ai_usage.provider_ids("anthropic", header, []) == {"trajectory.request.provider_account_id": "org-1"}
    assert asked == ["anthropic-organization-id"]
    assert ai_usage.provider_ids("openai", lambda name: None, ["arn:aws:bedrock:us-east-1:123456789012:x"]) == {}


def test_observations_are_strict_json():
    metrics = ai_usage.UsageMetrics()
    with pytest.raises(TypeError):
        metrics.add([ATTEMPT], dict(OBSERVATION, operation_name=object()))
    with pytest.raises(TypeError):
        metrics.add([ATTEMPT], {1: "x"})
    assert metrics.is_empty()


def test_unknown_metric_names_are_refused():
    with pytest.raises(ValueError) as error:
        ai_usage.UsageMetrics(["not.a.metric"])
    assert error.value.args == ("metric_unknown", "not.a.metric")


def test_an_observation_is_added_under_every_profile_or_none():
    metrics = ai_usage.UsageMetrics()
    with pytest.raises(ValueError) as error:
        metrics.add([ATTEMPT, BREAKDOWN], {"operation_name": "chat"})
    assert error.value.args[0] == "required_field_missing"
    assert error.value.args[2] == ATTEMPT
    assert metrics.is_empty()
    issues = metrics.add([ATTEMPT, BREAKDOWN], OBSERVATION)
    assert len(issues) == 2
    separate = ai_usage.UsageMetrics()
    separate.add([ATTEMPT], OBSERVATION)
    separate.add([BREAKDOWN], OBSERVATION)
    assert lines(metrics) == lines(separate)


def test_batch_attributes_go_under_each_observations_own():
    metrics = ai_usage.UsageMetrics(deployment_attributes={"service.name": "gateway", "trajectory.gateway.route": "a"})
    metrics.add([ATTEMPT], OBSERVATION, {"trajectory.gateway.route": "b"})
    taken = lines(metrics)
    assert taken and all("service:gateway" in line and "trajectory.gateway.route:b" in line for line in taken)
    assert metrics.take_dogstatsd(8192) == (b"", 0)


def test_metric_filter_and_otlp_take():
    metrics = ai_usage.UsageMetrics(["gen_ai.client.inference.usage.input_tokens"])
    metrics.add([ATTEMPT], OBSERVATION)
    [request] = metrics.take_otlp("scope", "1", 1, 2, 1 << 20)
    assert b"gen_ai.client.inference.usage.input_tokens" in request
    assert b"gen_ai.client.inference.duration" not in request
    assert metrics.take_otlp("scope", "1", 2, 3, 1 << 20) == []
