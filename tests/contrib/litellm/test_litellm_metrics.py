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


def _metric_calls(client, name):
    return [call for call in client.distribution.call_args_list if call.args[0] == name]


def test_provider_attempt_projection():
    client = mock.Mock(spec=MetricsClient)
    cost_calculator = mock.Mock(return_value=0.000004)
    clock = mock.Mock(side_effect=[10.0, 10.2, 10.8])
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
        "trajectory.gen_ai.client.operation.cost",
    ]
    assert [call.args[1] for call in client.distribution.call_args_list] == pytest.approx([0.8, 12, 2, 0.2, 0.000004])
    assert client.distribution.call_args_list[0].args[2] == base_tags
    assert client.distribution.call_args_list[1].args[2] == {**base_tags, "gen_ai.token.type": "input"}
    assert client.distribution.call_args_list[2].args[2] == {**base_tags, "gen_ai.token.type": "output"}
    assert client.distribution.call_args_list[3].args[2] == base_tags
    assert client.distribution.call_args_list[4].args[2] == {
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
