import asyncio
import datetime
import importlib
import json

import litellm
import pytest

from ddtrace import config
from ddtrace.contrib.internal.litellm import _usage_metrics
from ddtrace.contrib.internal.litellm import patch as litellm_patch
from ddtrace.contrib.internal.litellm._usage_metrics import GATEWAY_REQUEST
from ddtrace.contrib.internal.litellm._usage_metrics import PROVIDER_ATTEMPT
from ddtrace.contrib.internal.litellm._usage_metrics import TOKEN_BREAKDOWN
from ddtrace.contrib.internal.litellm._usage_metrics import UsageMetricsLogger
from ddtrace.contrib.internal.litellm._usage_metrics import error_type
from ddtrace.contrib.internal.litellm._usage_metrics import operation_name
from ddtrace.contrib.internal.litellm._usage_metrics import provider_name
from ddtrace.contrib.internal.litellm._usage_metrics import usage_fields
from ddtrace.contrib.internal.litellm._usage_metrics_writer import ai_usage
from ddtrace.contrib.internal.litellm.patch import patch
from ddtrace.contrib.internal.litellm.patch import unpatch
from ddtrace.trace import tracer


T0 = datetime.datetime(2026, 10, 8, 12, 0, 0)


def at(seconds):
    return T0 + datetime.timedelta(seconds=seconds)


CHAT_USAGE = {
    "prompt_tokens": 12,
    "completion_tokens": 7,
    "total_tokens": 19,
    "prompt_tokens_details": {"cached_tokens": 4},
    "completion_tokens_details": {"reasoning_tokens": 3},
}

# The Anthropic Messages route, as LiteLLM normalizes it: prompt_tokens includes cache reads and writes.
ANTHROPIC_USAGE = {
    "prompt_tokens": 60,
    "completion_tokens": 5,
    "cache_read_input_tokens": 20,
    "cache_creation_input_tokens": 30,
    "prompt_tokens_details": {
        "cached_tokens": 20,
        "cache_creation_tokens": 30,
        "cache_creation_token_details": {"ephemeral_5m_input_tokens": 18, "ephemeral_1h_input_tokens": 12},
    },
}

PLACEHOLDER_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

IDENTITY = {
    "user_api_key_user_id": "user-alice",
    "user_api_key_team_id": "team-ml",
    "user_api_key_alias": "alice-key",
}


class RecordingWriter:
    def __init__(self):
        self.records = []

    def record(self, profile_id, observation, deployment_attributes):
        self.records.append((profile_id, dict(observation), dict(deployment_attributes or {})))

    def of(self, profile_id):
        return [(observation, attributes) for p, observation, attributes in self.records if p == profile_id]


class NativeWriter(RecordingWriter):
    """Also projects every observation with the native module, which rejects any it cannot use."""

    def __init__(self):
        super().__init__()
        self.metrics = ai_usage.UsageMetrics()
        self.rejected = []

    def record(self, profile_id, observation, deployment_attributes):
        super().record(profile_id, observation, deployment_attributes)
        # The logger's hooks swallow errors, so keep rejections for the test to check.
        try:
            self.metrics.record(profile_id, observation, deployment_attributes)
        except Exception as e:
            self.rejected.append((profile_id, observation, e))


class UserAPIKeyAuth:
    user_id = "user-alice"
    team_id = "team-ml"
    key_alias = "alice-key"


def run(coroutine):
    return asyncio.run(coroutine)


class Proxy:
    """Drives a UsageMetricsLogger with the hook sequence a LiteLLM proxy produces for one client request."""

    def __init__(self, logger, call_id="call-1", route="gpt", call_type="acompletion", stream=False):
        self.logger = logger
        self.call_id = call_id
        self.route = route
        self.call_type = call_type
        self.stream = stream
        self.attempt_start = None

    def start(self):
        data = {"litellm_call_id": self.call_id, "model": self.route, "stream": self.stream}
        run(self.logger.async_pre_call_hook(UserAPIKeyAuth(), None, data, self.call_type))

    def attempt(self, start, model_group=None, provider="openai", model="gpt-4o-mini", model_id="deployment-1"):
        self.attempt_start = at(start)
        self.logger.log_pre_api_call(
            model,
            [],
            {
                "litellm_call_id": self.call_id,
                "call_type": self.call_type,
                "custom_llm_provider": provider,
                "model": model,
                "stream": self.stream,
                "api_call_start_time": self.attempt_start,
                "litellm_params": {
                    "metadata": dict(
                        IDENTITY,
                        model_group=model_group or self.route,
                        original_model_group=self.route,
                        model_info={"id": model_id},
                    )
                },
            },
        )

    def attempt_failed(self, exception):
        request_data = {"litellm_call_id": self.call_id, "metadata": dict(IDENTITY)}
        run(self.logger.async_post_call_failure_deployment_hook(request_data, exception, self.call_type))

    def kwargs(self, usage, cost, first_chunk=None, cache_hit=None, error_class=None, model="gpt-4o-mini"):
        payload = {
            "cache_hit": cache_hit,
            "response_cost": cost,
            "metadata": dict(IDENTITY, usage_object=usage),
            "error_information": {"error_class": error_class} if error_class else None,
        }
        return {
            "litellm_call_id": self.call_id,
            "call_type": self.call_type,
            "stream": self.stream,
            "model": model,
            "api_call_start_time": self.attempt_start,
            "completion_start_time": at(first_chunk) if first_chunk is not None else None,
            "original_response": json.dumps({"model": "gpt-4o-mini-2024-07-18"}) if not self.stream else None,
            "litellm_params": {"metadata": dict(IDENTITY, original_model_group=self.route)},
            "standard_logging_object": payload,
        }

    def succeeded(self, end, usage=CHAT_USAGE, cost=5.7e-6, **kwargs):
        run(self.logger.async_log_success_event(self.kwargs(usage, cost, **kwargs), None, T0, at(end)))

    def failed_midstream(self, end, usage, cost, error_class="ReadError"):
        kwargs = self.kwargs(usage, cost, error_class=error_class)
        run(self.logger.async_log_failure_event(kwargs, None, T0, at(end)))

    def failed(self, exception, **request_data):
        request_data["litellm_call_id"] = self.call_id
        run(self.logger.async_post_call_failure_hook(request_data, exception, UserAPIKeyAuth()))


@pytest.fixture
def writer():
    writer = NativeWriter() if ai_usage is not None else RecordingWriter()
    yield writer
    assert getattr(writer, "rejected", []) == []


@pytest.fixture
def logger(writer):
    return UsageMetricsLogger(writer, frozenset(), None, {})


def test_operation_and_provider_names():
    assert operation_name("acompletion") == "chat"
    assert operation_name("CallTypes.anthropic_messages") == "chat"
    assert operation_name("aresponses") == "chat"
    assert operation_name("atext_completion") == "text_completion"
    assert operation_name("aembedding") == "embeddings"
    assert operation_name("aimage_generation") is None
    assert provider_name("openai") == "openai"
    assert provider_name("text-completion-openai") == "openai"
    assert provider_name("bedrock_converse") == "aws.bedrock"
    assert provider_name("vertex_ai") == "gcp.vertex_ai"
    assert provider_name("Some Provider") == "some_provider"
    assert provider_name(None) is None


def test_error_types_are_identifiers():
    assert error_type("RateLimitError") == "rate_limit_error"
    assert error_type("APIConnectionError") == "api_connection_error"
    assert error_type("ClientDisconnected") == "client_disconnected"
    assert error_type(TimeoutError()) == "timeout_error"
    assert error_type("") is None


def test_usage_fields_read_the_chat_shape():
    assert usage_fields(CHAT_USAGE, embeddings=False) == {
        "input_tokens": 12,
        "input_basis": "includes_cache",
        "output_tokens": 7,
        "output_basis": "includes_reasoning",
        "cache_read_input_tokens": 4,
        "reasoning_output_tokens": 3,
    }
    assert usage_fields(ANTHROPIC_USAGE, embeddings=False) == {
        "input_tokens": 60,
        "input_basis": "includes_cache",
        "output_tokens": 5,
        "output_basis": "includes_reasoning",
        "cache_read_input_tokens": 20,
        "cache_write_input_tokens": 30,
        "cache_write_5m_input_tokens": 18,
        "cache_write_1h_input_tokens": 12,
    }


def test_usage_fields_treat_placeholders_as_unknown():
    assert usage_fields(PLACEHOLDER_USAGE, embeddings=False) == {}
    assert usage_fields(None, embeddings=False) == {}
    assert usage_fields({"prompt_tokens": 3, "completion_tokens": 0}, embeddings=True) == {
        "input_tokens": 3,
        "input_basis": "includes_cache",
    }


def test_successful_call(logger, writer):
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0)
    proxy.succeeded(1.5)

    [(attempt, attributes)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt == {
        "operation_name": "chat",
        "provider_name": "openai",
        "duration_seconds": 1.5,
        "streaming": False,
        "observation_point": "gateway",
        "request_model": "gpt-4o-mini",
        "response_model": "gpt-4o-mini-2024-07-18",
        "input_tokens": 12,
        "input_basis": "includes_cache",
        "output_tokens": 7,
        "output_basis": "includes_reasoning",
        "cache_read_input_tokens": 4,
        "reasoning_output_tokens": 3,
        "cost_usd": 5.7e-6,
        "cost_source": "estimated",
    }
    # Identity is opt-in.
    assert attributes == {}
    assert len(writer.of(TOKEN_BREAKDOWN)) == 1
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["operation_name"] == "chat"
    assert request["request_model"] == "gpt"
    assert request["provider_operations"] == 1
    assert request["provider_operation_coverage"] == "complete"
    # Retry and fallback counts cannot be inferred from the attempts, so they are left out.
    assert "retries" not in request and "fallbacks" not in request
    assert request["estimated_cost_usd"] == 5.7e-6
    assert "error_type" not in request
    assert logger._requests == {}


def test_retry_then_streamed_success(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    proxy.attempt_failed(Exception("rate limited"))
    proxy.attempt(0.8)
    logger.observe_stream("call-1", True, True)
    proxy.succeeded(1.0, first_chunk=0.85)

    failed, succeeded = (observation for observation, _ in writer.of(PROVIDER_ATTEMPT))
    assert failed["error_type"] == "exception"
    assert "input_tokens" not in failed and "cost_usd" not in failed
    # The success is measured from its own attempt, not from the start of the request.
    assert succeeded["duration_seconds"] == pytest.approx(0.2)
    assert succeeded["time_to_first_chunk_seconds"] == pytest.approx(0.05)
    assert "input_token_source" not in succeeded
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["provider_operations"] == 2
    assert "retries" not in request and "fallbacks" not in request


def test_stream_without_provider_usage_is_estimated(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    proxy.succeeded(1.0, first_chunk=0.1)
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["input_token_source"] == attempt["output_token_source"] == "estimated"


MESSAGE_START = (
    'event: message_start\ndata: {"type": "message_start", "message": {"model": "claude-haiku-4-5-20251001", '
    '"usage": {"input_tokens": 10, "cache_read_input_tokens": 20, "output_tokens": 1}}}\n\n'
)
MESSAGE_DELTA = b'event: message_delta\ndata: {"type": "message_delta", "usage": {"output_tokens": 5}}\n\n'


def test_anthropic_stream_usage_reads_the_raw_events():
    assert _usage_metrics.anthropic_stream_usage([MESSAGE_START, MESSAGE_DELTA]) == (
        True,
        True,
        "claude-haiku-4-5-20251001",
    )
    # A stream that ended without its final usage event: LiteLLM estimates the output.
    assert _usage_metrics.anthropic_stream_usage([MESSAGE_START]) == (True, False, "claude-haiku-4-5-20251001")
    assert _usage_metrics.anthropic_stream_usage([MESSAGE_START + MESSAGE_DELTA.decode()])[:2] == (True, True)
    assert _usage_metrics.anthropic_stream_usage(["data: [DONE]", b"\xff", 3]) == (False, False, None)


@pytest.mark.parametrize("final_usage, output_source", [(True, None), (False, "estimated")])
def test_anthropic_messages_stream_reports_each_side(logger, writer, final_usage, output_source):
    # The Anthropic Messages route does not pass through CustomStreamWrapper: the integration reads its raw events.
    proxy = Proxy(logger, call_type="anthropic_messages", stream=True, route="claude")
    proxy.start()
    proxy.attempt(0, provider="anthropic", model="claude-haiku-4-5")
    events = [MESSAGE_START, MESSAGE_DELTA] if final_usage else [MESSAGE_START]
    logger.observe_stream("call-1", *_usage_metrics.anthropic_stream_usage(events))
    proxy.succeeded(1.0, usage=ANTHROPIC_USAGE, cost=8.35e-5, first_chunk=0.2, model="claude-haiku-4-5")
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert "input_token_source" not in attempt
    assert attempt.get("output_token_source") == output_source
    assert attempt["cache_write_5m_input_tokens"] == 18
    assert attempt["response_model"] == "claude-haiku-4-5-20251001"


def test_a_stream_no_wrapper_saw_is_estimated(logger, writer):
    proxy = Proxy(logger, call_type="aresponses", stream=True)
    proxy.start()
    proxy.attempt(0)
    proxy.succeeded(1.0, first_chunk=0.2)
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["input_token_source"] == attempt["output_token_source"] == "estimated"


def test_attempt_span_is_marked(logger):
    proxy = Proxy(logger)
    proxy.start()
    with tracer.trace("litellm.request", resource="router.acompletion") as router:
        with tracer.trace("litellm.request", resource="acompletion") as span:
            proxy.attempt(0)
    assert span.get_tag(_usage_metrics.RECORDED_PROFILES_TAG) == _usage_metrics.ATTEMPT_PROFILES
    assert router.get_tag(_usage_metrics.RECORDED_PROFILES_TAG) == _usage_metrics.GATEWAY_PROFILES
    with tracer.trace("other") as other:
        proxy.attempt(0.1)
    assert other.get_tag(_usage_metrics.RECORDED_PROFILES_TAG) is None


def test_fallback(logger, writer):
    proxy = Proxy(logger, route="primary")
    proxy.start()
    proxy.attempt(0)
    proxy.attempt_failed(Exception())
    proxy.attempt(0.1, model_group="fallback-target", model_id="deployment-2")
    proxy.succeeded(0.5)
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["provider_operations"] == 2
    assert "retries" not in request and "fallbacks" not in request


def test_cache_hit(logger, writer):
    proxy = Proxy(logger)
    proxy.start()
    proxy.succeeded(0.01, cost=0.0, cache_hit=True)
    assert writer.of(PROVIDER_ATTEMPT) == []
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["cache_outcomes"] == ["hit"]
    assert request["estimated_cost_usd"] == 0
    assert request["provider_operations"] == 0


def test_total_failure(logger, writer):
    proxy = Proxy(logger)
    proxy.start()
    for start in (0, 0.1, 0.2):
        proxy.attempt(start)
        proxy.attempt_failed(TimeoutError())
    proxy.failed(TimeoutError())
    attempts = writer.of(PROVIDER_ATTEMPT)
    assert [observation["error_type"] for observation, _ in attempts] == ["timeout_error"] * 3
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["error_type"] == "timeout_error"
    assert request["provider_operations"] == 3
    assert "retries" not in request and "fallbacks" not in request
    # A request that failed for good was charged nothing, which is a real zero.
    assert request["estimated_cost_usd"] == 0
    assert logger._requests == {}


def test_client_disconnect(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    # The client left before the usage chunk: LiteLLM estimates both sides.
    logger.observe_stream("call-1", False, False)
    proxy.succeeded(
        0.3, usage={"prompt_tokens": 8, "completion_tokens": 5}, cost=4.2e-6, error_class="ClientDisconnected"
    )
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["error_type"] == "client_disconnected"
    assert attempt["input_token_source"] == attempt["output_token_source"] == "estimated"
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["error_type"] == "client_disconnected"
    assert request["estimated_cost_usd"] == 4.2e-6


def test_total_failure_seen_by_the_wrapper(logger, writer):
    # LiteLLM versions before async_post_call_failure_deployment_hook: the integration's wrapper sees each
    # attempt fail, and LiteLLM's one failure event for the request arrives late, after the retry started.
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0)
    first = proxy.attempt_start
    logger.attempt_failed({"litellm_call_id": "call-1"}, TimeoutError())
    proxy.attempt(0.1)
    late = proxy.kwargs(PLACEHOLDER_USAGE, 0.0, error_class="Timeout")
    late["api_call_start_time"] = first
    run(logger.async_log_failure_event(late, None, T0, at(0.05)))
    logger.attempt_failed({"litellm_call_id": "call-1"}, TimeoutError())
    proxy.attempt(0.2)
    logger.attempt_failed({"litellm_call_id": "call-1"}, TimeoutError())
    proxy.failed(TimeoutError())
    attempts = [observation for observation, _ in writer.of(PROVIDER_ATTEMPT)]
    assert [a["error_type"] for a in attempts] == ["timeout_error"] * 3
    assert [round(a["duration_seconds"], 6) >= 0 for a in attempts] == [True] * 3
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["provider_operations"] == 3
    assert "retries" not in request and "fallbacks" not in request


def test_failure_event_does_not_close_a_retry_that_started(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    first = proxy.attempt_start
    proxy.attempt_failed(Exception())
    proxy.attempt(0.1)
    # The first attempt's failure event, delivered after the retry started.
    late = proxy.kwargs(PLACEHOLDER_USAGE, 0.0, error_class="RateLimitError")
    late["api_call_start_time"] = first
    run(logger.async_log_failure_event(late, None, T0, at(0.05)))
    logger.observe_stream("call-1", True, True)
    proxy.succeeded(0.5, first_chunk=0.2)
    failed, succeeded = (observation for observation, _ in writer.of(PROVIDER_ATTEMPT))
    assert "error_type" in failed
    assert "error_type" not in succeeded
    assert succeeded["input_tokens"] == 12


PARTIAL_USAGE = {"prompt_tokens": 8, "completion_tokens": 1}


def charged(proxy, usage, cost):
    """The request data LiteLLM 1.104 gives its failure hook after a stream broke partway: the failure event's
    partial usage and the cost the proxy charges for it.
    """
    return {
        "combined_usage_object": litellm.Usage(**usage),
        "response_cost": cost,
        "standard_logging_object": proxy.kwargs(usage, cost, error_class="ReadError")["standard_logging_object"],
    }


@pytest.mark.parametrize("event_first", [True, False])
def test_midstream_provider_failure_is_charged(logger, writer, event_first):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    logger.observe_stream("call-1", False, False)
    if event_first:
        proxy.failed_midstream(0.4, usage=PARTIAL_USAGE, cost=1.8e-6)
    proxy.failed(Exception(), **charged(proxy, PARTIAL_USAGE, 1.8e-6))
    if not event_first:
        proxy.failed_midstream(0.4, usage=PARTIAL_USAGE, cost=1.8e-6)
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["input_tokens"] == 8
    assert attempt["cost_usd"] == 1.8e-6
    assert attempt["input_token_source"] == attempt["output_token_source"] == "estimated"
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["estimated_cost_usd"] == 1.8e-6
    assert request["error_type"] == "exception"
    assert logger._requests == {}


def test_failed_stream_without_a_failure_event(logger, writer):
    # Before LiteLLM 1.104, a native Anthropic Messages stream that breaks fires no failure event, and the proxy
    # charges nothing for it.
    proxy = Proxy(logger, call_type="anthropic_messages", stream=True)
    proxy.start()
    proxy.attempt(0, provider="anthropic", model="claude-haiku-4-5")
    proxy.failed(Exception())
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["error_type"] == "exception"
    assert "input_tokens" not in attempt
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["estimated_cost_usd"] == 0
    assert logger._requests == {}


def test_failure_event_after_the_attempt_is_closed_records_nothing(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    proxy.attempt_failed(Exception())
    proxy.failed_midstream(0.1, usage=PLACEHOLDER_USAGE, cost=0.0, error_class="RateLimitError")
    assert len(writer.of(PROVIDER_ATTEMPT)) == 1


def test_zero_cost_is_recorded_only_for_a_model_priced_at_zero(logger, writer, monkeypatch):
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, model="unpriced-model")
    proxy.succeeded(0.1, cost=0.0, model="unpriced-model")
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert "cost_usd" not in attempt
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert "estimated_cost_usd" not in request

    writer.records.clear()
    proxy = Proxy(logger, call_id="call-2")
    proxy.start()
    proxy.attempt(0)
    kwargs = proxy.kwargs(CHAT_USAGE, 0.0)
    kwargs["litellm_params"].update(input_cost_per_token=0.0, output_cost_per_token=0.0)
    run(logger.async_log_success_event(kwargs, None, T0, at(0.1)))
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["cost_usd"] == 0.0


def test_opt_in_tags(writer):
    tags = frozenset({"user", "team", "key_alias", "route", "destination"})
    logger = UsageMetricsLogger(writer, tags, "coding-agent", {"service.name": "gateway"})
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0)
    proxy.succeeded(0.1)
    expected = {
        "service.name": "gateway",
        "user.id": "user-alice",
        "trajectory.team.id": "team-ml",
        "trajectory.gateway.key.alias": "alice-key",
        "trajectory.gateway.route": "gpt",
        "trajectory.gateway.destination.id": "deployment-1",
        "trajectory.client_source": "coding-agent",
    }
    for _, _, attributes in writer.records:
        assert attributes == expected


def test_calls_outside_a_proxy_request_are_not_recorded(logger, writer):
    proxy = Proxy(logger)
    proxy.attempt(0)
    proxy.succeeded(0.1)
    assert writer.records == []


def test_concurrent_requests_are_kept_apart(logger, writer):
    first, second = Proxy(logger, call_id="a", route="r1"), Proxy(logger, call_id="b", route="r2")
    first.start()
    second.start()
    first.attempt(0)
    second.attempt(0.01)
    second.succeeded(0.2)
    first.succeeded(0.3)
    routes = sorted(observation["request_model"] for observation, _ in writer.of(GATEWAY_REQUEST))
    assert routes == ["r1", "r2"]
    assert len(writer.of(PROVIDER_ATTEMPT)) == 2


def test_hooks_never_raise(logger):
    logger.log_pre_api_call("m", [], {"litellm_call_id": object()})
    run(logger.async_pre_call_hook(None, None, {"litellm_call_id": "x", "model": 3}, object()))
    run(logger.async_log_success_event({"litellm_call_id": "x", "standard_logging_object": "nope"}, None, 1, 2))
    run(logger.async_log_failure_event({}, None, None, None))
    run(logger.async_post_call_failure_deployment_hook({}, None, None))
    run(logger.async_post_call_failure_hook({}, None, None))


@pytest.mark.skipif(ai_usage is None, reason="native ai_usage module not built")
def test_native_dogstatsd_series():
    writer = NativeWriter()
    logger = UsageMetricsLogger(writer, frozenset(), None, {})
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0)
    proxy.succeeded(1.5)
    lines = writer.metrics.take_dogstatsd()
    names = {line.split(":", 1)[0] for line in lines}
    assert "gen_ai.client.inference.duration" in names
    assert "trajectory.gen_ai.client.inference.usage.cost" in names
    assert "trajectory.gen_ai.gateway.request.estimated_cost" in names
    assert any(line.startswith("trajectory.gen_ai.client.inference.usage.cost:0.0000057|c|#") for line in lines)
    assert all("trajectory.profile:" in line and "trajectory.observation.point:gateway" in line for line in lines)
    assert writer.metrics.take_dogstatsd() == []


@pytest.mark.skipif(ai_usage is None, reason="native ai_usage module not built")
def test_native_rejections_and_filter():
    metrics = ai_usage.UsageMetrics(list(_usage_metrics.DEFAULT_METRICS))
    with pytest.raises(ValueError) as error:
        metrics.record(PROVIDER_ATTEMPT, {"operation_name": "chat"})
    assert error.value.args[0] == "required_field_missing"
    with pytest.raises(TypeError):
        metrics.record(PROVIDER_ATTEMPT, {"operation_name": object()})
    metrics.record(
        PROVIDER_ATTEMPT,
        {
            "operation_name": "chat",
            "provider_name": "openai",
            "duration_seconds": 1.0,
            "streaming": False,
            "input_tokens": 3,
            "output_tokens": 1,
            "observation_point": "gateway",
        },
    )
    names = {line.split(":", 1)[0] for line in metrics.take_dogstatsd()}
    # The token histograms are not exported by default.
    assert "gen_ai.client.inference.operation.input_tokens" not in names
    assert "gen_ai.client.inference.usage.input_tokens" in names
    assert metrics.take_otlp("scope", "1", 0, 1) is None


@pytest.mark.skipif(ai_usage is None, reason="native ai_usage module not built")
def test_patch_registers_the_logger_and_unpatch_removes_it(monkeypatch):
    monkeypatch.setitem(config.litellm, "usage_metrics_enabled", True)
    monkeypatch.setitem(config.litellm, "usage_metrics_exporter", "dogstatsd")
    monkeypatch.setitem(config.litellm, "usage_metrics_tags", "user,unknown")
    patch()
    try:
        logger = litellm._datadog_usage_metrics_logger
        assert logger in litellm.callbacks
        assert logger._tags == frozenset({"user"})
        assert litellm._datadog_usage_metrics_writer.status.value == "running"
    finally:
        unpatch()
    assert logger not in litellm.callbacks
    assert not hasattr(litellm, "_datadog_usage_metrics_logger")


@pytest.mark.parametrize(
    "provider, model, estimated",
    [
        ("replicate", "meta/llama-3", True),
        ("sagemaker", "jumpstart-model", True),
        ("ollama", "llama3", True),
        ("bedrock", "invoke/anthropic.claude-3-5-sonnet-20240620-v1:0", True),
        ("bedrock", "converse/anthropic.claude-3-5-sonnet-20240620-v1:0", False),
        ("sagemaker_chat", "jumpstart-model", False),
        ("openai", "gpt-4o-mini", False),
    ],
)
def test_locally_counted_providers_are_estimated(logger, writer, provider, model, estimated):
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, provider=provider, model=model)
    proxy.succeeded(0.5, usage={"prompt_tokens": 12, "completion_tokens": 7}, model=model)
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    sources = (attempt.get("input_token_source"), attempt.get("output_token_source"))
    assert sources == (("estimated", "estimated") if estimated else (None, None))


def test_reasoning_split_out_by_litellm_is_left_out(logger, writer):
    # LiteLLM counts an Anthropic reasoning part itself; the output total stays reported.
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, provider="anthropic", model="claude-sonnet-4-5")
    proxy.succeeded(0.5, model="claude-sonnet-4-5")
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert "reasoning_output_tokens" not in attempt
    assert attempt["output_tokens"] == 7 and "output_token_source" not in attempt


@pytest.mark.parametrize("reported", [True, False])
def test_stream_reasoning_is_kept_only_when_reported(logger, writer, reported):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    logger.observe_stream("call-1", True, True, reasoning_reported=reported)
    proxy.succeeded(0.5, first_chunk=0.1)
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert ("reasoning_output_tokens" in attempt) is reported


def test_usage_fields_read_reported_modalities():
    usage = {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "prompt_tokens_details": {"text_tokens": 4, "audio_tokens": 8, "image_tokens": 0, "cached_tokens": 0},
        "completion_tokens_details": {"audio_tokens": 5, "text_tokens": 2},
    }
    fields = usage_fields(usage, embeddings=False)
    assert fields["input_tokens_by_modality"] == {"text": 4, "audio": 8}
    assert fields["output_tokens_by_modality"] == {"text": 2, "audio": 5}
    assert "input_tokens_by_modality" not in usage_fields(usage, embeddings=False, modality=False)
    # Parts that add up to more than the total are not used.
    usage["prompt_tokens_details"]["text_tokens"] = 40
    assert "input_tokens_by_modality" not in usage_fields(usage, embeddings=False)
    assert "output_tokens_by_modality" not in usage_fields(usage, embeddings=True)


def test_reported_modalities_are_recorded(logger, writer):
    proxy = Proxy(logger)
    proxy.start()
    proxy.attempt(0, model="gpt-4o-audio-preview")
    usage = {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "prompt_tokens_details": {"audio_tokens": 8, "text_tokens": 4},
    }
    proxy.succeeded(0.5, usage=usage, model="gpt-4o-audio-preview")
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["input_tokens_by_modality"] == {"text": 4, "audio": 8}


def test_streamed_response_model_comes_from_the_stream(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    logger.observe_stream("call-1", False, False, "gpt-4o-mini-2024-07-18")
    logger.observe_stream("call-1", True, True, "ignored-later-model")
    proxy.succeeded(0.5, first_chunk=0.1)
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["response_model"] == "gpt-4o-mini-2024-07-18"
    assert "input_token_source" not in attempt


class StreamWrapper:
    """The attributes of LiteLLM's CustomStreamWrapper that the chunk wrapper reads."""

    def __init__(self, provider, call_id="call-1"):
        self.custom_llm_provider = provider
        self.logging_obj = type("LoggingObj", (), {"litellm_call_id": call_id})()


def chunk(prompt=None, completion=None, finish_reason=None, model=None, reasoning=None):
    usage = None
    if prompt is not None or completion is not None:
        usage = {"prompt_tokens": prompt, "completion_tokens": completion}
        if reasoning is not None:
            usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return {"usage": usage, "choices": [{"finish_reason": finish_reason}], "model": model}


@pytest.fixture
def installed(logger, monkeypatch):
    monkeypatch.setattr(litellm, "_datadog_usage_metrics_logger", logger, raising=False)
    return logger


def feed(wrapper, *chunks):
    for item in chunks:
        litellm_patch.traced_chunk_creator(lambda chunk: chunk, wrapper, (), {"chunk": item})


@pytest.mark.parametrize(
    "chunks, input_source, output_source",
    [
        # Anthropic: message_start reports the input and a placeholder output; the final chunk the real output.
        ([chunk(60, 1, model="claude-haiku-4-5-20251001"), chunk(60, 5, "end_turn")], None, None),
        ([chunk(60, 1, model="claude-haiku-4-5-20251001")], None, "estimated"),
        ([chunk(model="claude-haiku-4-5-20251001")], "estimated", "estimated"),
    ],
)
def test_anthropic_chat_stream_reports_each_side(installed, writer, chunks, input_source, output_source):
    proxy = Proxy(installed, stream=True)
    proxy.start()
    proxy.attempt(0, provider="anthropic", model="claude-haiku-4-5")
    feed(StreamWrapper("anthropic"), *chunks)
    proxy.succeeded(0.5, usage=ANTHROPIC_USAGE, first_chunk=0.1, model="claude-haiku-4-5")
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert (attempt.get("input_token_source"), attempt.get("output_token_source")) == (input_source, output_source)
    assert attempt["response_model"] == "claude-haiku-4-5-20251001"


def test_openai_chat_stream_usage_chunk(installed, writer):
    proxy = Proxy(installed, stream=True)
    proxy.start()
    proxy.attempt(0)
    feed(StreamWrapper("openai"), chunk(model="gpt-4o-mini-2024-07-18"), chunk(12, 7, reasoning=3))
    proxy.succeeded(0.5, first_chunk=0.1)
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert "input_token_source" not in attempt and "output_token_source" not in attempt
    assert attempt["reasoning_output_tokens"] == 3
    assert attempt["response_model"] == "gpt-4o-mini-2024-07-18"


def test_responses_stream_usage_reads_the_raw_events():
    created = 'data: {"type": "response.created", "response": {"model": "gpt-4o-mini-2024-07-18"}}'
    completed = json.dumps(
        {
            "type": "response.completed",
            "response": {
                "model": "gpt-4o-mini-2024-07-18",
                "usage": {"input_tokens": 12, "output_tokens": 7, "output_tokens_details": {"reasoning_tokens": 3}},
            },
        }
    )
    assert _usage_metrics.responses_stream_usage(created) == (False, False, False, "gpt-4o-mini-2024-07-18")
    assert _usage_metrics.responses_stream_usage(completed) == (True, True, True, "gpt-4o-mini-2024-07-18")
    unreported = json.dumps({"type": "response.completed", "response": {"usage": None}})
    assert _usage_metrics.responses_stream_usage(unreported) == (False, False, False, None)
    assert _usage_metrics.responses_stream_usage('data: {"type": "response.output_text.delta"}') == (
        False,
        False,
        False,
        None,
    )


def test_stream_wrappers_never_raise(installed):
    litellm_patch.traced_chunk_creator(lambda chunk: chunk, object(), (), {"chunk": object()})
    litellm_patch.traced_anthropic_stream_events(lambda *a: None, None, (object(), object()), {})
    litellm_patch.traced_responses_stream_event(lambda chunk: chunk, object(), (b"\xff",), {})


@pytest.mark.skipif(ai_usage is None, reason="native ai_usage module not built")
def test_unpatch_removes_the_logger_from_every_callback_list(monkeypatch):
    monkeypatch.setitem(config.litellm, "usage_metrics_enabled", True)
    monkeypatch.setitem(config.litellm, "usage_metrics_exporter", "dogstatsd")
    messages = [{"role": "user", "content": "hi"}]
    patch()
    try:
        first = litellm._datadog_usage_metrics_logger
        # A call copies the logger into LiteLLM's input, success and failure lists.
        litellm.completion(model="gpt-4o-mini", messages=messages, mock_response="hello")
    finally:
        unpatch()
    for name in litellm_patch._CALLBACK_LISTS:
        assert all(callback is not first for callback in getattr(litellm, name, None) or []), name
    assert litellm_patch._wrapped_stream_methods == {}
    patch()
    try:
        second = litellm._datadog_usage_metrics_logger
        litellm.completion(model="gpt-4o-mini", messages=messages, mock_response="hello")
        # The new logger gets the success events: no stale logger of its class blocks it.
        success = list(litellm.success_callback) + list(getattr(litellm, "_async_success_callback", []))
        assert any(callback is second for callback in success)
    finally:
        unpatch()


def test_unpatch_restores_the_stream_methods():
    try:
        module = importlib.import_module(litellm_patch._ANTHROPIC_PASSTHROUGH_MODULE)
    except ImportError:
        pytest.skip("the LiteLLM proxy is not installed")
    handler = module.AnthropicPassthroughLoggingHandler
    original = vars(handler)["_build_complete_streaming_response"]
    litellm_patch._wrap_anthropic_stream(module)
    assert vars(handler)["_build_complete_streaming_response"] is not original
    litellm_patch._unwrap_stream_methods()
    assert vars(handler)["_build_complete_streaming_response"] is original
    assert isinstance(original, staticmethod)


def test_a_retry_no_hook_reported_is_recorded_when_the_request_succeeds(logger, writer):
    # Before async_post_call_failure_deployment_hook, nothing reports a failed embeddings retry; the next attempt
    # starting is the only sign that it ended.
    proxy = Proxy(logger, call_type="aembedding", route="embed")
    proxy.start()
    proxy.attempt(0, model="text-embedding-3-small")
    proxy.attempt(0.3, model="text-embedding-3-small")
    proxy.succeeded(0.5, usage={"prompt_tokens": 3, "completion_tokens": 0}, model="text-embedding-3-small")
    failed, succeeded = (observation for observation, _ in writer.of(PROVIDER_ATTEMPT))
    assert failed["error_type"] == "error"
    assert failed["duration_seconds"] == pytest.approx(0.3)
    assert "error_type" not in succeeded and succeeded["input_tokens"] == 3
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["provider_operations"] == 2
    assert logger._requests == {}
