from collections import Counter
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from unittest import mock

import pytest

from ddtrace.contrib.internal.litellm.metrics import LiteLLMProviderMetrics
from ddtrace.internal.metrics import MetricsClient
from tests.contrib.litellm.utils import get_cassette_name
from tests.utils import override_global_config


@pytest.fixture
def provider_metrics(litellm):
    client = mock.Mock(spec=MetricsClient)
    litellm._datadog_integration._provider_metrics = LiteLLMProviderMetrics(enabled=True, client=client)
    return client


@pytest.fixture
def openai_router_server():
    requests = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests[self.path] += 1
            if self.path.startswith("/always-fail/") or (
                self.path.startswith("/fail-once/") and requests[self.path] == 1
            ):
                body = json.dumps(
                    {
                        "error": {
                            "message": "synthetic upstream failure",
                            "type": "server_error",
                        }
                    }
                ).encode()
                self.send_response(500)
            else:
                body = json.dumps(
                    {
                        "id": "chatcmpl-local",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "gpt-4o-mini",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                ).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, message_format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:{}".format(server.server_port), requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _metric_calls(client, name):
    return [call for call in client.distribution.call_args_list if call.args[0] == name]


def _count_calls(client, name):
    return [call for call in client.increment.call_args_list if call.args[0] == name]


def test_provider_attempt_projection():
    client = mock.Mock(spec=MetricsClient)
    cost_calculator = mock.Mock(return_value=0.000004)
    clock = mock.Mock(side_effect=[10.0, 10.2, 10.5, 10.8])
    metrics = LiteLLMProviderMetrics(
        enabled=True,
        client=client,
        clock=clock,
        cost_calculator=cost_calculator,
    )

    attempt = metrics.start_attempt("completion", "openai/gpt-4o-mini", streaming=True)
    metrics.observe_first_chunk(
        attempt,
        {
            "model": "gpt-4o-mini-2024-07-18",
            "usage": {"prompt_tokens": 12, "completion_tokens": 2},
        },
    )
    metrics.observe_chunk(attempt, {"model": "gpt-4o-mini-2024-07-18"})
    metrics.finish_attempt(
        attempt,
        {"openai/gpt-4o-mini": ("gpt-4o-mini", "openai")},
    )

    base_tags = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "gpt-4o-mini",
        "gen_ai.response.model": "gpt-4o-mini-2024-07-18",
    }
    assert [call.args[0] for call in client.distribution.call_args_list] == [
        "gen_ai.client.operation.duration",
        "gen_ai.client.token.usage",
        "gen_ai.client.token.usage",
        "gen_ai.client.operation.time_to_first_chunk",
        "gen_ai.client.operation.time_per_output_chunk",
        "trajectory.gen_ai.client.operation.cost",
    ]
    assert [call.args[1] for call in client.distribution.call_args_list] == pytest.approx(
        [0.8, 12, 2, 0.2, 0.3, 0.000004]
    )
    assert client.distribution.call_args_list[0].args[2] == base_tags
    assert client.distribution.call_args_list[1].args[2] == {**base_tags, "gen_ai.token.type": "input"}
    assert client.distribution.call_args_list[2].args[2] == {**base_tags, "gen_ai.token.type": "output"}
    assert client.distribution.call_args_list[3].args[2] == base_tags
    assert client.distribution.call_args_list[4].args[2] == base_tags
    assert client.distribution.call_args_list[5].args[2] == {
        **base_tags,
        "trajectory.cost.source": "gateway_calculated",
    }
    cost_calculator.assert_called_once()


def test_provider_reported_zero_values_are_emitted():
    client = mock.Mock(spec=MetricsClient)
    cost_calculator = mock.Mock()
    clock = mock.Mock(side_effect=[4.0, 4.0])
    metrics = LiteLLMProviderMetrics(
        enabled=True,
        client=client,
        clock=clock,
        cost_calculator=cost_calculator,
    )

    attempt = metrics.start_attempt("text_completion", "openrouter/openai/gpt-4o-mini", streaming=False)
    metrics.finish_attempt(
        attempt,
        {"openrouter/openai/gpt-4o-mini": ("openai/gpt-4o-mini", "openrouter")},
        response={
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost": 0,
            }
        },
    )

    assert [call.args[1] for call in client.distribution.call_args_list] == [0, 0, 0, 0]
    assert client.distribution.call_args_list[-1].args[2]["trajectory.cost.source"] == "provider_reported"
    cost_calculator.assert_not_called()


def test_terminal_error_emits_only_duration_with_bounded_error_type():
    client = mock.Mock(spec=MetricsClient)
    clock = mock.Mock(side_effect=[5.0, 5.25])
    metrics = LiteLLMProviderMetrics(enabled=True, client=client, clock=clock)

    attempt = metrics.start_attempt("acompletion", "anthropic/claude-haiku-4-5", streaming=False)
    metrics.finish_attempt(
        attempt,
        {"anthropic/claude-haiku-4-5": ("claude-haiku-4-5", "anthropic")},
        exception=TimeoutError("request 123 timed out"),
    )

    client.distribution.assert_called_once_with(
        "gen_ai.client.operation.duration",
        0.25,
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": "claude-haiku-4-5",
            "error.type": "litellm.timeout_error",
        },
    )


def test_missing_usage_is_omitted_instead_of_reported_as_zero():
    client = mock.Mock(spec=MetricsClient)
    cost_calculator = mock.Mock()
    clock = mock.Mock(side_effect=[1.0, 1.1])
    metrics = LiteLLMProviderMetrics(
        enabled=True,
        client=client,
        clock=clock,
        cost_calculator=cost_calculator,
    )

    attempt = metrics.start_attempt("completion", "openai/custom-model", streaming=False)
    metrics.finish_attempt(
        attempt,
        {"openai/custom-model": ("custom-model", "openai")},
        response={"model": "custom-model", "usage": {}},
    )

    client.distribution.assert_called_once()
    assert client.distribution.call_args.args[0] == "gen_ai.client.operation.duration"
    cost_calculator.assert_not_called()


def test_disabled_or_unresolved_attempts_emit_nothing():
    client = mock.Mock(spec=MetricsClient)
    clock = mock.Mock()
    disabled = LiteLLMProviderMetrics(enabled=False, client=client, clock=clock)
    assert disabled.start_attempt("completion", "gpt-4o-mini", streaming=False) is None
    clock.assert_not_called()

    enabled = LiteLLMProviderMetrics(enabled=True, client=client, clock=mock.Mock(side_effect=[1.0, 2.0]))
    attempt = enabled.start_attempt("completion", "custom-model", streaming=False)
    enabled.finish_attempt(attempt, {})
    client.distribution.assert_not_called()


def test_gateway_request_projection_uses_explicit_router_facts():
    client = mock.Mock(spec=MetricsClient)
    clock = mock.Mock(side_effect=[10.0, 10.1, 10.5, 11.0])
    metrics = LiteLLMProviderMetrics(enabled=True, client=client, clock=clock)
    response = {
        "model": "gpt-4o-mini",
        "_hidden_params": {
            "additional_headers": {
                "x-litellm-attempted-retries": 2,
                "x-litellm-attempted-fallbacks": "0",
            }
        },
    }

    request = metrics.start_gateway_request("router.completion", "support-model", streaming=False)
    attempt = metrics.start_attempt("completion", "openai/gpt-4o-mini", streaming=False)
    metrics.finish_attempt(
        attempt,
        {"openai/gpt-4o-mini": ("gpt-4o-mini", "openai")},
        response=response,
    )
    metrics.finish_gateway_request(request, response=response)
    metrics.detach_gateway_request(request)

    base_tags = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "support-model",
    }
    assert _metric_calls(client, "trajectory.gen_ai.gateway.request.duration")[0].args[1:] == (
        1.0,
        base_tags,
    )
    assert _metric_calls(client, "trajectory.gen_ai.gateway.request.provider_operations")[0].args[1:] == (
        1,
        {**base_tags, "trajectory.provider.operation.coverage": "complete"},
    )
    assert _metric_calls(client, "trajectory.gen_ai.gateway.request.retries")[0].args[1:] == (2, base_tags)
    assert _metric_calls(client, "trajectory.gen_ai.gateway.request.fallbacks")[0].args[1:] == (0, base_tags)


def test_gateway_fallback_omits_branch_scoped_retry_count():
    client = mock.Mock(spec=MetricsClient)
    clock = mock.Mock(side_effect=[3.0, 3.5])
    metrics = LiteLLMProviderMetrics(enabled=True, client=client, clock=clock)
    response = {
        "_hidden_params": {
            "additional_headers": {
                "x-litellm-attempted-retries": 1,
                "x-litellm-attempted-fallbacks": 1,
            }
        }
    }

    request = metrics.start_gateway_request(
        "router.completion",
        "support-model",
        streaming=False,
        fallbacks_configured=True,
    )
    metrics.finish_gateway_request(request, response=response)
    metrics.detach_gateway_request(request)

    assert not _metric_calls(client, "trajectory.gen_ai.gateway.request.retries")
    assert _metric_calls(client, "trajectory.gen_ai.gateway.request.fallbacks")[0].args[1] == 1


def test_explicit_cache_hit_is_not_counted_as_provider_operation():
    client = mock.Mock(spec=MetricsClient)
    clock = mock.Mock(side_effect=[1.0, 1.1, 1.2, 1.3])
    metrics = LiteLLMProviderMetrics(enabled=True, client=client, clock=clock)
    response = {"_hidden_params": {"cache_hit": True}}

    request = metrics.start_gateway_request("router.completion", "cached-model", streaming=False)
    attempt = metrics.start_attempt("completion", "openai/cached-model", streaming=False)
    metrics.finish_attempt(
        attempt,
        {"openai/cached-model": ("cached-model", "openai")},
        response=response,
    )
    metrics.finish_gateway_request(request, response=response)
    metrics.detach_gateway_request(request)

    assert not _metric_calls(client, "gen_ai.client.operation.duration")
    assert _metric_calls(client, "trajectory.gen_ai.gateway.request.provider_operations")[0].args[1] == 0
    assert _count_calls(client, "trajectory.gen_ai.gateway.cache.operations")[0].args[1:] == (
        1,
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "cached-model",
            "trajectory.cache.outcome": "hit",
        },
    )


def test_gateway_terminal_error_uses_explicit_retry_count():
    client = mock.Mock(spec=MetricsClient)
    clock = mock.Mock(side_effect=[2.0, 2.5])
    metrics = LiteLLMProviderMetrics(enabled=True, client=client, clock=clock)
    error = TimeoutError("request failed")
    error.num_retries = 3

    request = metrics.start_gateway_request("router.acompletion", "chat-model", streaming=False)
    metrics.finish_gateway_request(request, exception=error)
    metrics.detach_gateway_request(request)

    duration = _metric_calls(client, "trajectory.gen_ai.gateway.request.duration")[0]
    assert duration.args[2]["error.type"] == "litellm.timeout_error"
    assert _metric_calls(client, "trajectory.gen_ai.gateway.request.retries")[0].args[1] == 3
    assert not _metric_calls(client, "trajectory.gen_ai.gateway.request.fallbacks")


def test_litellm_completion_emits_metrics_when_tracing_is_disabled(
    litellm,
    provider_metrics,
    request_vcr,
):
    with override_global_config({"_tracing_enabled": False}):
        with request_vcr.use_cassette("completion.yaml"):
            litellm.completion(
                model="gpt-3.5-turbo",
                messages=[{"content": "Hey, what is up?", "role": "user"}],
            )

    duration_calls = _metric_calls(provider_metrics, "gen_ai.client.operation.duration")
    token_calls = _metric_calls(provider_metrics, "gen_ai.client.token.usage")
    assert len(duration_calls) == 1
    assert len(token_calls) == 2
    assert duration_calls[0].args[1] >= 0
    assert duration_calls[0].args[2]["gen_ai.provider.name"] == "openai"
    assert {call.args[2]["gen_ai.token.type"] for call in token_calls} == {"input", "output"}


def test_litellm_stream_emits_one_duration_and_time_to_first_chunk(
    litellm,
    provider_metrics,
    request_vcr,
):
    with request_vcr.use_cassette(get_cassette_name(stream=True, n=1)):
        response = litellm.completion(
            model="gpt-3.5-turbo",
            messages=[{"content": "Hey, what is up?", "role": "user"}],
            stream=True,
        )
        list(response)

    assert len(_metric_calls(provider_metrics, "gen_ai.client.operation.duration")) == 1
    assert len(_metric_calls(provider_metrics, "gen_ai.client.operation.time_to_first_chunk")) == 1
    assert len(_metric_calls(provider_metrics, "gen_ai.client.operation.time_per_output_chunk")) >= 1


async def test_litellm_async_stream_emits_one_duration_and_time_to_first_chunk(
    litellm,
    provider_metrics,
    request_vcr,
):
    with request_vcr.use_cassette(get_cassette_name(stream=True, n=1)):
        response = await litellm.acompletion(
            model="gpt-3.5-turbo",
            messages=[{"content": "Hey, what is up?", "role": "user"}],
            stream=True,
        )
        async for _ in response:
            pass

    assert len(_metric_calls(provider_metrics, "gen_ai.client.operation.duration")) == 1
    assert len(_metric_calls(provider_metrics, "gen_ai.client.operation.time_to_first_chunk")) == 1
    assert len(_metric_calls(provider_metrics, "gen_ai.client.operation.time_per_output_chunk")) >= 1


def test_router_does_not_duplicate_provider_attempt_metrics(
    litellm,
    provider_metrics,
    request_vcr,
    router,
):
    with request_vcr.use_cassette(get_cassette_name(stream=False, n=1)):
        router.completion(
            model="gpt-3.5-turbo",
            messages=[{"content": "Hey, what is up?", "role": "user"}],
        )

    assert len(_metric_calls(provider_metrics, "gen_ai.client.operation.duration")) == 1
    assert len(_metric_calls(provider_metrics, "trajectory.gen_ai.gateway.request.duration")) == 1
    assert _metric_calls(provider_metrics, "trajectory.gen_ai.gateway.request.provider_operations")[0].args[1] == 1


def test_router_stream_emits_partial_provider_operation_coverage(
    litellm,
    provider_metrics,
    request_vcr,
    router,
):
    with request_vcr.use_cassette(get_cassette_name(stream=True, n=1)):
        response = router.completion(
            model="gpt-3.5-turbo",
            messages=[{"content": "Hey, what is up?", "role": "user"}],
            stream=True,
        )
        list(response)

    assert len(_metric_calls(provider_metrics, "trajectory.gen_ai.gateway.request.duration")) == 1
    provider_operations = _metric_calls(
        provider_metrics,
        "trajectory.gen_ai.gateway.request.provider_operations",
    )[0]
    assert provider_operations.args[1] == 1
    assert provider_operations.args[2]["trajectory.provider.operation.coverage"] == "partial"


def test_real_router_retry_emits_request_scoped_retry_count(
    litellm,
    provider_metrics,
    openai_router_server,
):
    base_url, requests = openai_router_server
    router = litellm.Router(
        model_list=[
            {
                "model_name": "retry-model",
                "litellm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_base": "{}/fail-once/v1".format(base_url),
                    "api_key": "test-key",
                    "max_retries": 0,
                },
            }
        ],
        num_retries=1,
        retry_after=0,
    )

    router.completion(
        model="retry-model",
        messages=[{"content": "hello", "role": "user"}],
    )

    assert sum(requests.values()) == 2
    assert _metric_calls(provider_metrics, "trajectory.gen_ai.gateway.request.retries")[0].args[1] == 1
    assert _metric_calls(provider_metrics, "trajectory.gen_ai.gateway.request.fallbacks")[0].args[1] == 0
    provider_operations = _metric_calls(
        provider_metrics,
        "trajectory.gen_ai.gateway.request.provider_operations",
    )[0]
    assert provider_operations.args[1] == 2
    assert provider_operations.args[2]["trajectory.provider.operation.coverage"] == "complete"


def test_real_router_fallback_omits_branch_scoped_retry_count(
    litellm,
    provider_metrics,
    openai_router_server,
):
    base_url, requests = openai_router_server
    router = litellm.Router(
        model_list=[
            {
                "model_name": "primary-model",
                "litellm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_base": "{}/always-fail/v1".format(base_url),
                    "api_key": "test-key",
                    "max_retries": 0,
                },
            },
            {
                "model_name": "fallback-model",
                "litellm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_base": "{}/success/v1".format(base_url),
                    "api_key": "test-key",
                    "max_retries": 0,
                },
            },
        ],
        num_retries=1,
        retry_after=0,
        fallbacks=[{"primary-model": ["fallback-model"]}],
    )

    router.completion(
        model="primary-model",
        messages=[{"content": "hello", "role": "user"}],
    )

    assert sum(requests.values()) == 3
    assert not _metric_calls(provider_metrics, "trajectory.gen_ai.gateway.request.retries")
    assert _metric_calls(provider_metrics, "trajectory.gen_ai.gateway.request.fallbacks")[0].args[1] == 1
    assert _metric_calls(provider_metrics, "trajectory.gen_ai.gateway.request.provider_operations")[0].args[1] == 3


def test_real_router_cache_hit_is_not_a_provider_operation(
    litellm,
    provider_metrics,
    openai_router_server,
):
    base_url, requests = openai_router_server
    original_cache = litellm.cache
    litellm.cache = litellm.Cache(type="local")
    router = litellm.Router(
        model_list=[
            {
                "model_name": "cached-model",
                "litellm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_base": "{}/success/v1".format(base_url),
                    "api_key": "test-key",
                    "max_retries": 0,
                },
            }
        ]
    )
    request = {
        "model": "cached-model",
        "messages": [{"content": "cache this exact request", "role": "user"}],
        "caching": True,
    }
    try:
        router.completion(**request)
        provider_metrics.reset_mock()
        router.completion(**request)
    finally:
        litellm.cache = original_cache

    assert sum(requests.values()) == 1
    assert not _metric_calls(provider_metrics, "gen_ai.client.operation.duration")
    assert _metric_calls(provider_metrics, "trajectory.gen_ai.gateway.request.provider_operations")[0].args[1] == 0
    cache_call = _count_calls(provider_metrics, "trajectory.gen_ai.gateway.cache.operations")[0]
    assert cache_call.args[1] == 1
    assert cache_call.args[2]["trajectory.cache.outcome"] == "hit"
