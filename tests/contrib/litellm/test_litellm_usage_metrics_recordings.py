"""Replay the hook calls of a running LiteLLM proxy and check what the integration records against what happened.

usage_metrics_recordings.json holds 38 scenarios recorded from litellm[proxy]==1.104.0 serving a fake
OpenAI and Anthropic provider: streams with and without provider usage, the Anthropic Messages and Responses routes,
retries, fallbacks, failures, timeouts, disconnects, cache hits, and unpriced and zero-priced models. Each scenario
holds the hooks the proxy called, in order, with the fields of their arguments the integration reads, and the ground
truth of the recording: how many requests the provider received and answered, how many requests the client made,
and what the proxy charged for each.
"""

import asyncio
import copy
import datetime
import json
import pathlib
from types import SimpleNamespace

import litellm
import pytest

from ddtrace.contrib.internal.litellm._usage_metrics import GATEWAY_REQUEST
from ddtrace.contrib.internal.litellm._usage_metrics import OPT_IN_TAGS
from ddtrace.contrib.internal.litellm._usage_metrics import PROVIDER_ATTEMPT
from ddtrace.contrib.internal.litellm._usage_metrics import UsageMetricsLogger
from ddtrace.contrib.internal.litellm._usage_metrics_writer import ai_usage


RECORDINGS = json.loads((pathlib.Path(__file__).parent / "usage_metrics_recordings.json").read_text())
SCENARIOS = {scenario["scenario"]: scenario for scenario in RECORDINGS["scenarios"]}
DATETIMES = ("api_call_start_time", "completion_start_time")

# The token sources of the successful attempt, where the recording shows the stream's chunks.
TOKEN_SOURCES = {
    "chat_nonstream_openai": (None, None),
    "chat_nonstream_anthropic": (None, None),
    "chat_stream_openai_include_usage": (None, None),
    # The proxy asks for the usage chunk itself.
    "chat_stream_openai_no_include_usage": (None, None),
    "chat_stream_openai_provider_omits_usage": ("estimated", "estimated"),
    "chat_stream_anthropic_include_usage": (None, None),
    "retry_then_success_stream": (None, None),
}


class Writer:
    def __init__(self):
        self.records = []
        self.rejected = []
        self.metrics = ai_usage.UsageMetrics() if ai_usage is not None else None

    def record(self, profile_id, observation, deployment_attributes):
        self.records.append((profile_id, dict(observation), dict(deployment_attributes or {})))
        if self.metrics is not None:
            try:
                self.metrics.record(profile_id, observation, deployment_attributes)
            except Exception as e:
                self.rejected.append((profile_id, observation, e))

    def of(self, profile_id):
        return [(observation, attributes) for p, observation, attributes in self.records if p == profile_id]


def exception(name):
    return type(name, (Exception,), {})()


def arguments(recorded):
    kwargs = copy.deepcopy(recorded)
    for key in DATETIMES:
        if isinstance(kwargs.get(key), str):
            kwargs[key] = datetime.datetime.fromisoformat(kwargs[key])
    if isinstance(kwargs.get("exception"), str):
        kwargs["exception"] = exception(kwargs["exception"])
    return kwargs


def replay(logger, scenario):
    for step in scenario["steps"]:
        hook = step["hook"]
        if hook == "async_pre_call_hook":
            user = SimpleNamespace(**step["user"])
            asyncio.run(logger.async_pre_call_hook(user, None, dict(step["data"]), step["call_type"]))
        elif hook == "log_pre_api_call":
            kwargs = arguments(step["kwargs"])
            logger.log_pre_api_call(kwargs["model"], [], kwargs)
        elif hook == "observe_stream":
            logger.observe_stream(*step["args"])
        elif hook == "async_post_call_failure_deployment_hook":
            request_data = copy.deepcopy(step["request_data"])
            asyncio.run(
                logger.async_post_call_failure_deployment_hook(request_data, exception(step["exception"]), None)
            )
        elif hook in ("async_log_success_event", "async_log_failure_event"):
            start = datetime.datetime.fromisoformat(step["start_time"])
            end = datetime.datetime.fromisoformat(step["end_time"])
            asyncio.run(getattr(logger, hook)(arguments(step["kwargs"]), None, start, end))
        elif hook == "async_post_call_failure_hook":
            request_data = copy.deepcopy(step["request_data"])
            if "combined_usage_object" in request_data:
                request_data["combined_usage_object"] = litellm.Usage(**request_data["combined_usage_object"])
            asyncio.run(logger.async_post_call_failure_hook(request_data, exception(step["exception"]), None))
        else:
            raise AssertionError(f"unknown recorded hook {hook}")


def charges(values):
    """Charges as a comparable multiset: an omitted charge is None and sorts last."""
    return sorted(values, key=lambda value: (value is None, value or 0.0))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_recorded_proxy_calls(name):
    scenario = SCENARIOS[name]
    truth = scenario["truth"]
    writer = Writer()
    logger = UsageMetricsLogger(writer, OPT_IN_TAGS, None, {})
    replay(logger, scenario)

    attempts = [observation for observation, _ in writer.of(PROVIDER_ATTEMPT)]
    requests = [observation for observation, _ in writer.of(GATEWAY_REQUEST)]
    # One attempt per request the provider received, failed when the provider did not serve it.
    assert len(attempts) == truth["attempts"], attempts
    assert sum(1 for attempt in attempts if "error_type" in attempt) == truth["attempt_errors"], attempts
    # One gateway request per client request, charged what the proxy charged.
    assert len(requests) == truth["client_requests"], requests
    recorded = [request.get("estimated_cost_usd") for request in requests]
    expected = charges(truth["charges"])
    assert [value is None for value in charges(recorded)] == [value is None for value in expected], recorded
    assert [value for value in charges(recorded) if value is not None] == pytest.approx(
        [value for value in expected if value is not None], rel=1e-9
    )
    assert all(request["provider_operations"] <= truth["attempts"] for request in requests)
    # Every request is released, and the native projector accepted every observation.
    assert logger._requests == {}
    assert writer.rejected == []

    if name in TOKEN_SOURCES:
        [served] = [attempt for attempt in attempts if "error_type" not in attempt]
        sources = (served.get("input_token_source"), served.get("output_token_source"))
        assert sources == TOKEN_SOURCES[name], served


def test_recorded_identity_and_models():
    writer = Writer()
    logger = UsageMetricsLogger(writer, OPT_IN_TAGS, None, {})
    replay(logger, SCENARIOS["chat_stream_openai_include_usage"])
    [(attempt, attributes)] = writer.of(PROVIDER_ATTEMPT)
    # The model the provider streamed back, not the route.
    assert attempt["request_model"] == "gpt-4o-mini"
    assert attempt["response_model"] == "gpt-4o-mini-2024-07-18"
    assert attributes["user.id"] == "user-alice"
    assert attributes["trajectory.team.id"] == "team-ml"
    assert attributes["trajectory.gateway.key.alias"] == "alice-key"
    assert attributes["trajectory.gateway.route"] == "gpt-4o-mini"

    writer = Writer()
    logger = UsageMetricsLogger(writer, OPT_IN_TAGS, None, {})
    replay(logger, SCENARIOS["identity_key_without_alias"])
    assert writer.records
    for _, _, attributes in writer.records:
        # A key with no alias sends no alias tag.
        assert "trajectory.gateway.key.alias" not in attributes


def test_recorded_failed_stream_is_charged():
    # A stream the provider broke partway: the attempt keeps its partial usage, and the request records what the
    # proxy charged for it.
    writer = Writer()
    logger = UsageMetricsLogger(writer, OPT_IN_TAGS, None, {})
    replay(logger, SCENARIOS["provider_disconnect_midstream_openai"])
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["error_type"]
    assert attempt["input_tokens"] > 0
    assert attempt["input_token_source"] == attempt["output_token_source"] == "estimated"
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["estimated_cost_usd"] == pytest.approx(attempt["cost_usd"])
