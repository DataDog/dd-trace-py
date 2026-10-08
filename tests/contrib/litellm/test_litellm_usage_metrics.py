import asyncio
import datetime
import json

import litellm
import pytest

from ddtrace import config
from ddtrace.contrib.internal.litellm import _usage_metrics
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

    def failed(self, exception):
        run(self.logger.async_post_call_failure_hook({"litellm_call_id": self.call_id}, exception, UserAPIKeyAuth()))


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
    assert request["retries"] == 0
    assert request["fallbacks"] == 0
    assert request["estimated_cost_usd"] == 5.7e-6
    assert "error_type" not in request
    assert logger._requests == {}


def test_retry_then_streamed_success(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    proxy.attempt_failed(Exception("rate limited"))
    proxy.attempt(0.8)
    logger.mark_provider_usage("call-1")
    proxy.succeeded(1.0, first_chunk=0.85)

    failed, succeeded = (observation for observation, _ in writer.of(PROVIDER_ATTEMPT))
    assert failed["error_type"] == "exception"
    assert "input_tokens" not in failed and "cost_usd" not in failed
    # The success is measured from its own attempt, not from the start of the request.
    assert succeeded["duration_seconds"] == pytest.approx(0.2)
    assert succeeded["time_to_first_chunk_seconds"] == pytest.approx(0.05)
    assert "input_token_source" not in succeeded
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert (request["provider_operations"], request["retries"], request["fallbacks"]) == (2, 1, 0)


def test_stream_without_provider_usage_is_estimated(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    proxy.succeeded(1.0, first_chunk=0.1)
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["input_token_source"] == attempt["output_token_source"] == "estimated"


def test_anthropic_messages_stream_is_reported(logger, writer):
    # The Anthropic Messages route does not pass through CustomStreamWrapper: its usage comes in its own events.
    proxy = Proxy(logger, call_type="anthropic_messages", stream=True, route="claude")
    proxy.start()
    proxy.attempt(0, provider="anthropic", model="claude-haiku-4-5")
    proxy.succeeded(1.0, usage=ANTHROPIC_USAGE, cost=8.35e-5, first_chunk=0.2, model="claude-haiku-4-5")
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert "input_token_source" not in attempt
    assert attempt["cache_write_5m_input_tokens"] == 18


def test_attempt_span_is_marked(logger):
    proxy = Proxy(logger)
    proxy.start()
    with tracer.trace("litellm.request") as span:
        proxy.attempt(0)
    assert span.get_tag(_usage_metrics.RECORDED_PROFILES_TAG) == _usage_metrics.ATTEMPT_PROFILES
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
    assert request["fallbacks"] == 1
    assert "retries" not in request


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
    assert request["retries"] == 2
    assert "estimated_cost_usd" not in request
    assert logger._requests == {}


def test_client_disconnect(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    logger.mark_provider_usage("call-1")
    proxy.succeeded(
        0.3, usage={"prompt_tokens": 8, "completion_tokens": 5}, cost=4.2e-6, error_class="ClientDisconnected"
    )
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["error_type"] == "client_disconnected"
    assert attempt["input_token_source"] == attempt["output_token_source"] == "estimated"
    [(request, _)] = writer.of(GATEWAY_REQUEST)
    assert request["error_type"] == "client_disconnected"
    assert request["estimated_cost_usd"] == 4.2e-6


def test_midstream_provider_failure(logger, writer):
    proxy = Proxy(logger, stream=True)
    proxy.start()
    proxy.attempt(0)
    proxy.failed(Exception())
    proxy.failed_midstream(0.4, usage={"prompt_tokens": 8, "completion_tokens": 1}, cost=1.8e-6)
    [(attempt, _)] = writer.of(PROVIDER_ATTEMPT)
    assert attempt["error_type"] == "read_error"
    assert attempt["input_tokens"] == 8
    assert attempt["output_token_source"] == "estimated"
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
