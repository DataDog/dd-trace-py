import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
import logging
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from litellm import ModelResponse
from litellm import Usage
import pytest

from ddtrace.contrib.internal.litellm._gateway_metadata import cache_tags
from ddtrace.contrib.internal.litellm._gateway_metadata import request_tags
from ddtrace.contrib.internal.litellm._gateway_metadata import response_tags
from ddtrace.contrib.internal.litellm._gateway_metadata import route_tags
from ddtrace.contrib.internal.litellm._gateway_usage import DatadogSink
from ddtrace.contrib.internal.litellm._gateway_usage import Usage as RecordedUsage
from ddtrace.contrib.internal.litellm._gateway_usage import UsageRecord
from ddtrace.contrib.internal.litellm._gateway_usage import context_tokens_bucket
from ddtrace.contrib.internal.litellm._gateway_usage import normalize_usage
from ddtrace.contrib.internal.litellm.gateway import CORRELATION_FIELD
from ddtrace.contrib.internal.litellm.gateway import GatewayAttribution
from ddtrace.contrib.internal.litellm.gateway import configured_callback
from ddtrace.internal import logger as internal_logger
from ddtrace.vendor.dogstatsd import DogStatsd


def make_callback(*, sink=None, max_pending=10000, pending_ttl=3600, **kwargs):
    callback = GatewayAttribution(**kwargs)
    if sink is not None:
        callback._sink = sink
    callback._max_pending = max_pending
    callback._pending_ttl = pending_ttl
    return callback


def response(usage=None, deployment="dep-1", **kwargs):
    result = ModelResponse(model="gpt-4o-2024-08-06", usage=usage, **kwargs)
    result._hidden_params["model_id"] = deployment
    return result


async def start(callback, user="user-1", data=None, **auth):
    data = data if data is not None else {}
    await callback.async_pre_call_hook(SimpleNamespace(user_id=user, **auth), None, data, "completion")
    return data


async def finish(callback, data, result=None, **kwargs):
    now = datetime.now(timezone.utc)
    await callback.async_log_success_event(
        {"litellm_params": data, **kwargs}, result if result is not None else response(), now, now
    )


def test_partition_cache_ttl_and_reasoning():
    result = normalize_usage(
        Usage(
            prompt_tokens=1000,
            completion_tokens=120,
            prompt_tokens_details={
                "cached_tokens": 400,
                "cache_creation_tokens": 300,
                "cache_creation_token_details": {
                    "ephemeral_5m_input_tokens": 100,
                    "ephemeral_1h_input_tokens": 200,
                },
            },
            completion_tokens_details={"reasoning_tokens": 70},
        )
    )
    assert not result.issues
    assert sum(result.quantities.values()) == 1120
    assert result.quantities["input_uncached_tokens"] == 300
    assert result.diagnostics["context_tokens"] == 1000
    assert result.diagnostics["reasoning_output_tokens"] == 70


@pytest.mark.parametrize(
    "lower,upper,bucket,next_bucket",
    [
        (0, 32_000, "0_32000", "32001_128000"),
        (32_001, 128_000, "32001_128000", "128001_200000"),
        (128_001, 200_000, "128001_200000", "200001_256000"),
        (200_001, 256_000, "200001_256000", "256001_272000"),
        (256_001, 272_000, "256001_272000", "272001_512000"),
        (272_001, 512_000, "272001_512000", "512001_plus"),
    ],
)
def test_global_context_bucket_boundaries(lower, upper, bucket, next_bucket):
    for tokens in (lower, upper - 1, upper):
        usage = normalize_usage({"prompt_tokens": tokens, "completion_tokens": 999_999})
        assert context_tokens_bucket(usage) == bucket
    usage = normalize_usage({"prompt_tokens": upper + 1, "completion_tokens": 0})
    assert context_tokens_bucket(usage) == next_bucket


@pytest.mark.parametrize("tokens", [None, True, -1, 1.5, float("nan"), float("inf"), "32000", 2**53 + 1])
def test_context_bucket_unknown_for_missing_or_invalid_input(tokens):
    usage = normalize_usage({"prompt_tokens": tokens, "completion_tokens": 0})
    assert context_tokens_bucket(usage) == "unknown"


@pytest.mark.parametrize("issue", ["invalid_usage", "inconsistent_usage", "inconsistent_cache_ttl"])
def test_context_bucket_unknown_for_inconsistent_usage(issue):
    usage = RecordedUsage(diagnostics={"context_tokens": 32_000}, issues={issue})
    assert context_tokens_bucket(usage) == "unknown"


def test_context_bucket_includes_native_anthropic_caches_without_double_counting():
    usage = normalize_usage(
        {
            "input_tokens": 30_000,
            "cache_read_input_tokens": 1_000,
            "cache_creation_input_tokens": 1_001,
            "output_tokens": 3,
        },
        "anthropic_messages",
    )
    assert context_tokens_bucket(usage) == "32001_128000"
    normalized = normalize_usage(
        {"prompt_tokens": 32_000, "cache_read_input_tokens": 20_000, "completion_tokens": 30_000}
    )
    assert context_tokens_bucket(normalized) == "0_32000"
    assert context_tokens_bucket(RecordedUsage(diagnostics={"context_tokens": 2**53})) == "512001_plus"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "vertex_ai", "new-provider"])
async def test_context_bucket_is_global_and_cannot_be_set_by_client(provider):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, data={"metadata": {"ai.context_tokens.bucket": "forged"}})
    result = response(Usage(prompt_tokens=128_001, completion_tokens=1))
    result._hidden_params["custom_llm_provider"] = provider
    await finish(callback, data, result)
    assert records[0].tags["ai.context_tokens.bucket"] == "128001_200000"


async def test_unknown_context_bucket_for_failure_and_missing_usage():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await callback.async_post_call_failure_hook(data, RuntimeError("PRIVATE"), None)
    data = await start(callback)
    await finish(callback, data, SimpleNamespace(model="unknown-model"))
    assert [record.tags["ai.context_tokens.bucket"] for record in records] == ["unknown", "unknown"]


@pytest.mark.parametrize(
    "raw,issue",
    [
        (None, "missing_usage"),
        ({"input_tokens": 3}, "unsupported_usage_shape"),
        ({"prompt_tokens": -1, "completion_tokens": 1}, "invalid_usage"),
        ({"prompt_tokens": True, "completion_tokens": 1}, "invalid_usage"),
        ({"prompt_tokens": float("nan"), "completion_tokens": 1}, "invalid_usage"),
        (
            {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
            "inconsistent_usage",
        ),
        (
            {
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "prompt_tokens_details": {"audio_tokens": 2},
            },
            "multimodal_partition_unsupported",
        ),
    ],
)
def test_bad_or_ambiguous_usage_is_not_zero_filled(raw, issue):
    result = normalize_usage(raw)
    assert issue in result.issues
    assert result.quantities == {}


def test_unknown_ttl_and_explicit_zero():
    raw = {
        "prompt_tokens": 100,
        "completion_tokens": 0,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 20,
    }
    result = normalize_usage(raw)
    assert result.quantities["input_uncached_tokens"] == 50
    assert result.quantities["input_cache_write_unknown_ttl_tokens"] == 30
    assert result.quantities["input_cache_write_5m_tokens"] == 0
    assert "cache_write_ttl_unknown" in result.issues
    assert result.quantities["output_tokens"] == 0


async def test_identity_is_authenticated_no_secrets_and_email_on_by_default():
    records = []
    callback = make_callback(sink=records.append, auth_metadata_keys=("cost_center",))
    forged = {
        "user": "victim",
        "messages": [{"content": "SECRET PROMPT"}],
        "metadata": {
            CORRELATION_FIELD: "spoof",
            "user_api_key_user_id": "victim",
            "usr.email": "victim@example.test",
            "cost_center": "spoof",
        },
        "litellm_metadata": {CORRELATION_FIELD: "spoof"},
    }
    data = await start(
        callback,
        data=forged,
        user_email="real@example.test",
        team_id="team-1",
        api_key="sk-do-not-log",
        metadata={"cost_center": "eng", "secret": "hidden"},
    )
    assert data["metadata"][CORRELATION_FIELD] != "spoof"
    await finish(
        callback,
        data,
        response(
            Usage(prompt_tokens=20, completion_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        ),
    )
    record = records[0]
    assert record.tags["usr.id"] == "user-1"
    assert record.tags["team.id"] == "team-1"
    assert record.tags["ai.enrichment.cost_center"] == "eng"
    assert record.tags["ai.attribution.status"] == "observed"
    assert record.tags["usr.email"] == "real@example.test"
    assert not any(word in repr(record) for word in ("SECRET", "sk-do-not-log", "victim"))


async def test_email_capture_can_be_disabled():
    records = []
    callback = make_callback(sink=records.append, capture_email=False)
    data = await start(callback, user_email="real@example.test")
    await finish(callback, data)
    assert "usr.email" not in records[0].tags
    assert records[0].tags["usr.id"] == "user-1"


@pytest.mark.parametrize("email", [None, "", "sk-PRIVATE", {"email": "PRIVATE"}])
async def test_default_email_capture_omits_missing_or_invalid_email(email):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, user_email=email, data={"metadata": {"usr.email": "spoofed@example.test"}})
    await finish(callback, data)
    assert "usr.email" not in records[0].tags
    assert "PRIVATE" not in repr(records)
    assert "spoofed" not in repr(records)


@pytest.mark.parametrize("user", [None, "shared-service"])
async def test_end_user_fallback_preserves_authenticated_identity(user):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, user=user, end_user_id="claimed-user", data={"user": "ignored-raw-user"})
    await finish(
        callback,
        data,
        response(
            Usage(prompt_tokens=20, completion_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        ),
    )
    tags = records[0].tags
    assert tags["usr.id"] == (user or "claimed-user")
    assert tags["ai.identity.source"] == ("gateway_auth" if user else "litellm_end_user")
    assert tags["ai.end_user.id"] == "claimed-user"
    assert tags["ai.end_user.trust"] == "unverified"
    assert tags["ai.attribution.status"] == ("observed" if user else "incomplete")
    assert ("authenticated_user_unknown" in tags.get("ai.attribution.issues", "")) == (user is None)
    assert "ignored-raw-user" not in repr(records)


@pytest.mark.parametrize(
    "end_user",
    [
        None,
        "",
        " padded ",
        "sk-secret",
        "Bearer secret",
        "id\nheader",
        "x" * 257,
        {},
        True,
        '{"session_id":"private"}',
        '["private"]',
    ],
)
async def test_invalid_or_filtered_end_user_is_not_recovered_from_request(end_user):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(
        callback,
        user=None,
        end_user_id=end_user,
        data={"user": "raw-user", "metadata": {"user_id": "raw-metadata-user"}},
    )
    await finish(callback, data)
    tags = records[0].tags
    assert tags["ai.identity.source"] == "unknown"
    assert "usr.id" not in tags
    assert "ai.end_user.id" not in tags
    assert "authenticated_user_unknown" in tags["ai.attribution.issues"]


@pytest.mark.parametrize("user", [None, "authenticated-user"])
async def test_end_user_capture_can_be_disabled(user):
    records = []
    callback = make_callback(sink=records.append, capture_end_user=False)
    data = await start(callback, user=user, end_user_id="private-user")
    await finish(callback, data)
    assert records[0].tags.get("usr.id") == user
    assert records[0].tags["ai.identity.source"] == ("gateway_auth" if user else "unknown")
    assert "ai.end_user.id" not in records[0].tags
    assert "private-user" not in repr(records)


@pytest.mark.parametrize("outcome", ["failure", "shutdown"])
async def test_end_user_fallback_stays_unverified_on_incomplete_requests(outcome):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, user=None, end_user_id="claimed-user")
    if outcome == "failure":
        await callback.async_post_call_failure_hook(data, Exception("private failure"), None)
    else:
        callback.close()
    tags = records[0].tags
    assert tags["usr.id"] == "claimed-user"
    assert tags["ai.identity.source"] == "litellm_end_user"
    assert tags["ai.end_user.trust"] == "unverified"
    assert tags["ai.attribution.status"] == "incomplete"


async def test_response_selected_deployment_and_client_credentials():
    records = []
    callback = make_callback(sink=records.append)
    for data, deployment in [({}, "fallback"), ({"api_key": "sk-client"}, "dep-1")]:
        data = await start(callback, data=data)
        await finish(callback, data, response(deployment=deployment))
        assert records[-1].tags["ai.gateway.deployment_id"] == deployment
        assert not any(key.startswith("ai.billing.") for key in records[-1].tags)
        assert "sk-client" not in repr(records)


async def test_stream_final_only_duplicates_and_cached_response():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await callback.async_log_stream_event({"litellm_params": data}, response(), None, None)
    assert not records
    await finish(callback, data, response(Usage(prompt_tokens=9, completion_tokens=4)), stream=True)
    await finish(callback, data, response(), stream=True)
    assert len(records) == 1
    assert records[0].tags["ai.usage.source"] == "litellm_normalized_may_estimate"
    data = await start(callback)
    await finish(callback, data, response(Usage(prompt_tokens=9, completion_tokens=4)), cache_hit=True)
    assert records[-1].usage.quantities == {}
    assert records[-1].tags["ai.request.outcome"] == "gateway_cache_hit"
    assert records[-1].tags["ai.context_tokens.bucket"] == "unknown"


async def test_concurrent_users_failure_and_missing_identity():
    records = []
    callback = make_callback(sink=records.append)

    async def request(index):
        data = await start(callback, user=str(index))
        await asyncio.sleep(0)
        await finish(callback, data, response(Usage(prompt_tokens=index, completion_tokens=0)))

    await asyncio.gather(*(request(i) for i in range(30)))
    assert len(records) == 30
    assert all(int(r.tags["usr.id"]) == r.usage.diagnostics["input_tokens"] for r in records)
    data = await start(callback, user=None)
    await callback.async_post_call_failure_hook(data, Exception("sk-secret PROMPT"), None)
    assert records[-1].tags["ai.request.outcome"] == "error"
    assert "usr.id" not in records[-1].tags
    assert "sk-secret" not in repr(records)
    assert not callback._pending


def test_threaded_hooks_keep_user_usage_and_route_together():
    records = []
    callback = make_callback(sink=records.append)

    def ingress(index):
        return asyncio.run(start(callback, user=str(index)))

    def route(item):
        index, data = item
        asyncio.run(
            callback.async_pre_call_deployment_hook(
                {**data, "model_info": {"id": f"dep-{index % 2}"}, "organization": f"org-{index % 2}"}, "completion"
            )
        )
        # This synchronous hook may run outside the ingress event loop.
        callback.log_pre_api_call("gpt-4o", [], {"litellm_params": data})

    def complete(index):
        result = response(
            Usage(
                prompt_tokens=index + 1,
                completion_tokens=2,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
            deployment=None,
        )
        asyncio.run(finish(callback, requests[index], result))

    with ThreadPoolExecutor(max_workers=8) as executor:
        requests = list(executor.map(ingress, range(200)))
        list(executor.map(route, enumerate(requests)))
        # Concurrent duplicate terminal callbacks must still emit exactly once.
        list(executor.map(complete, list(range(199, -1, -1)) * 2))

    assert len(records) == 200
    assert all("ai.request.id" not in record.tags and "ai.response.id" not in record.tags for record in records)
    assert {record.tags["usr.id"] for record in records} == {str(index) for index in range(200)}
    for record in records:
        index = int(record.tags["usr.id"])
        assert record.tags["ai.gateway.deployment_id"] == f"dep-{index % 2}"
        assert record.tags["ai.route.organization"] == f"org-{index % 2}"
        assert record.usage.quantities["input_uncached_tokens"] == index + 1
        assert record.usage.quantities["output_tokens"] == 2
        assert record.usage.diagnostics["attempts"] == 1
    assert not callback._pending


async def test_bounded_state_expiration_shutdown_and_broken_exporter():
    records = []
    callback = make_callback(sink=records.append, max_pending=1, pending_ttl=0.001)
    await start(callback)
    await asyncio.sleep(0.01)
    await start(callback)
    await start(callback)
    assert len(records) == 2
    callback.close()
    assert len(records) == 3
    assert not callback._pending
    callback = make_callback(sink=lambda _: 1 / 0)
    await finish(callback, await start(callback))  # Never turns a successful request into a 500.


@pytest.mark.parametrize(
    "kwargs",
    [
        {"auth_metadata_keys": ("api_key",)},
        {"auth_metadata_keys": ("a.b",)},
        {"capture_email": "false"},
        {"capture_end_user": "false"},
        {"auth_metadata_keys": "cost_center"},
    ],
)
def test_bad_configuration(kwargs):
    with pytest.raises(ValueError):
        make_callback(**kwargs)


def test_metrics_are_additive_include_tags_and_preserve_fractional_seconds():
    client = Mock(spec=DogStatsd)
    sink = DatadogSink(client)
    record = UsageRecord(
        {"usr.id": "u", "ai.route.api_key_id": "key_123"},
        RecordedUsage(quantities={"output_tokens": 2}, diagnostics={"input_audio_length_seconds": 0.25}),
    )
    sink(record)
    sink(record)
    totals = defaultdict(float)
    for call in client.increment.call_args_list:
        name = call.args[0]
        totals[name] += call.args[1] if len(call.args) > 1 else 1
        assert call.kwargs["tags"] == ["ai.route.api_key_id:key_123", "usr.id:u"]
    assert dict(totals) == {
        "ai_gateway.usage.output_tokens": 4,
        "ai_gateway.observed.input_audio_length_seconds": 0.5,
        "ai_gateway.requests": 2,
    }


@pytest.mark.parametrize(
    "operation,raw",
    [
        (
            "anthropic_messages",
            {
                "input_tokens": 60,
                "output_tokens": 25,
                "cache_read_input_tokens": 40,
                "cache_creation_input_tokens": 0,
            },
        ),
        (
            "aresponses",
            {
                "input_tokens": 100,
                "output_tokens": 25,
                "input_tokens_details": {"cached_tokens": 40},
                "output_tokens_details": {"reasoning_tokens": 10},
            },
        ),
    ],
)
def test_native_provider_input_semantics(operation, raw):
    result = normalize_usage(raw, operation)
    if operation == "anthropic_messages":
        assert result.quantities["input_uncached_tokens"] == 60
        assert not result.issues
    else:
        assert "input_uncached_tokens" not in result.quantities
        assert result.issues == {"cache_write_detail_missing"}
    assert result.quantities["input_cache_read_tokens"] == 40
    assert result.quantities["output_tokens"] == 25
    assert result.diagnostics["context_tokens"] == 100


async def test_multimodal_request_without_usage_breakdown_is_not_allocatable():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(
        callback,
        data={
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "data:secret-image"}}],
                }
            ]
        },
    )
    await finish(callback, data, response(Usage(prompt_tokens=100, completion_tokens=10)))
    assert records[0].usage.quantities == {}
    assert records[0].usage.diagnostics["input_tokens"] == 100
    assert "multimodal_partition_unsupported" in records[0].tags["ai.attribution.issues"]
    assert "secret-image" not in repr(records)


async def test_request_and_response_tiers_stay_separate_and_model_suffix_is_preserved():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, data={"service_tier": "priority"})
    await finish(callback, data, response(Usage(prompt_tokens=1, completion_tokens=0), service_tier="future-tier"))
    tags = records[0].tags
    assert tags["ai.request.service_tier"] == "priority"
    assert tags["ai.observed.service_tier"] == "future-tier"
    assert tags["ai.model"] == tags["ai.response.model"] == "gpt-4o-2024-08-06"
    assert not any(key.startswith("ai.billing.") for key in tags)


async def test_unknown_sdk_callbacks_and_conflicting_tokens_do_not_export():
    records = []
    callback = make_callback(sink=records.append)
    await finish(callback, {"metadata": {CORRELATION_FIELD: "client-invented"}})
    data = await start(callback)
    data["litellm_metadata"][CORRELATION_FIELD] = "mismatched"
    await finish(callback, data)
    assert records == []
    callback.close()
    assert len(records) == 1


@pytest.mark.parametrize(
    "value", ["sk-secret", "Bearer secret", " sk-secret", "secret\x7f", "", "x" * 257, "id\nheader", {}, [], True]
)
def test_invalid_pricing_values_are_not_exported(value):
    assert not request_tags({"service_tier": value}, "x")
    assert not response_tags({"service_tier": value})
    assert not response_tags({"_hidden_params": {"provider_specific_fields": {"traffic_type": value}}})
    assert not cache_tags({"cache_control": {"type": value, "ttl": value}}, "x")


@pytest.mark.parametrize(
    "config",
    [
        [],
        "invalid",
        {"billing_scopes": []},
        {"capture_email": "true"},
        {"capture_end_user": "false"},
        {"unexpected": 1},
    ],
)
def test_invalid_file_configuration_fails_closed(tmp_path, monkeypatch, config):
    import json
    from unittest.mock import patch

    path = tmp_path / "attribution.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG", str(path))
    with patch("ddtrace.contrib.internal.litellm.gateway.log.warning") as warning:
        callback = configured_callback()
    warning.assert_called_once_with("Invalid gateway attribution configuration; optional identity enrichment disabled")
    assert not callback._capture_email
    assert not callback._capture_end_user
    assert not callback._auth_metadata_keys


def test_missing_and_invalid_configuration_identity_defaults(monkeypatch):
    monkeypatch.delenv("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG", raising=False)
    assert configured_callback()._capture_email
    assert configured_callback()._capture_end_user
    monkeypatch.setenv("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG", "/nonexistent/attribution-config.json")
    assert not configured_callback()._capture_email
    assert not configured_callback()._capture_end_user


def test_file_configuration_can_disable_end_user_capture(tmp_path, monkeypatch):
    path = tmp_path / "attribution.json"
    path.write_text('{"capture_end_user": false}')
    monkeypatch.setenv("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG", str(path))
    assert configured_callback()._capture_email
    assert not configured_callback()._capture_end_user


def test_file_configuration_can_disable_email_capture(tmp_path, monkeypatch):
    path = tmp_path / "attribution.json"
    path.write_text('{"capture_email": false}')
    monkeypatch.setenv("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG", str(path))
    assert not configured_callback()._capture_email
    assert configured_callback()._capture_end_user


async def test_forked_worker_drops_parent_requests(monkeypatch):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    monkeypatch.setattr("ddtrace.contrib.internal.litellm.gateway.os.getpid", lambda: callback._pid + 1)
    await finish(callback, data)
    callback.close()
    assert not records
    assert not callback._pending


async def test_hook_error_does_not_fail_gateway(monkeypatch, caplog):
    callback = make_callback()

    def fail(*args):
        raise RuntimeError("PRIVATE PROMPT sk-secret")

    monkeypatch.setattr(callback, "_start", fail)
    assert await callback.async_pre_call_hook(None, None, {}, "completion") is None
    assert "PRIVATE PROMPT" not in caplog.text
    assert "sk-secret" not in caplog.text


async def test_selected_route_attempts_and_explicit_retry_fallback_counts():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    for fallback, retry in ((0, 0), (0, 1), (1, 0), (1, 1)):
        data["metadata"].update(attempted_fallbacks=fallback, attempted_retries=retry)
        await callback.async_pre_call_deployment_hook({**data, "model_info": {"id": "dep-1"}}, "completion")
    await finish(callback, data, response(Usage(prompt_tokens=4, completion_tokens=2), deployment=None))
    assert records[0].tags["ai.gateway.deployment_id"] == "dep-1"
    assert records[0].usage.diagnostics["attempts"] == 4
    assert records[0].usage.diagnostics["retries"] == 2
    assert records[0].usage.diagnostics["fallbacks"] == 1
    assert "additional_attempt_usage_unknown" in records[0].tags["ai.attribution.issues"]


@pytest.mark.parametrize("retry,fallback", [(None, None), (True, True), (-1, 1), ("1", "1")])
async def test_missing_or_invalid_retry_markers_are_not_guessed(retry, fallback):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    data["metadata"].update(attempted_retries=retry, attempted_fallbacks=fallback)
    await callback.async_pre_call_deployment_hook({**data, "model_info": {"id": "dep-1"}}, "completion")
    await finish(callback, data)
    assert "retries" not in records[0].usage.diagnostics
    assert "fallbacks" not in records[0].usage.diagnostics


async def test_failure_keeps_observed_retry_counts_and_ingress_markers_are_not_trusted():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, data={"metadata": {"attempted_retries": 99, "attempted_fallbacks": 99}})
    assert "attempted_retries" not in data["metadata"]
    assert "attempted_fallbacks" not in data["metadata"]
    data["metadata"].update(attempted_retries=1, attempted_fallbacks=0)
    await callback.async_pre_call_deployment_hook(data, "completion")
    await callback.async_post_call_failure_hook(data, RuntimeError("PRIVATE"), None)
    assert records[0].usage.diagnostics == {"attempts": 1, "retries": 1, "fallbacks": 0}
    assert not records[0].usage.quantities


def test_public_constructor_does_not_accept_billing_overrides():
    with pytest.raises(TypeError):
        GatewayAttribution(billing_scopes={"deployment": {"provider": "openai"}})


async def test_response_property_errors_are_not_exported():
    class BrokenUsage:
        @property
        def prompt_tokens(self):
            raise ValueError("PRIVATE PROMPT sk-secret")

    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await finish(callback, data, SimpleNamespace(usage=BrokenUsage()))
    assert len(records) == 1
    assert records[0].tags["ai.attribution.issues"] == "unsupported_callback_shape"
    assert "PRIVATE PROMPT" not in repr(records)
    assert "sk-secret" not in repr(records)


@pytest.mark.parametrize(
    "provider", ["anthropic", "openai", "bedrock", "vertex_ai", "azure_ai", "gemini", "oci", "Future_Provider"]
)
def test_raw_route_dimensions_without_provider_mapping(provider):
    data = {
        "custom_llm_provider": provider,
        "model": "vendor/model-version",
        "organization": "org-real",
        "vertex_project": "project-real",
        "aws_region_name": "future-region",
    }
    tags = route_tags({**data, "api_key": "sk-PRIVATE", "vertex_credentials": "PRIVATE credentials"})
    assert tags["ai.route.provider"] == provider
    for key in ("model", "organization", "vertex_project", "aws_region_name"):
        assert tags[f"ai.route.{key}"] == data[key]
    assert not any(key.startswith("ai.billing.") for key in tags)
    assert "PRIVATE" not in repr(tags)


def test_model_prefix_does_not_invent_a_provider():
    assert route_tags({"model": "bedrock/anthropic.claude"}) == {"ai.route.model": "bedrock/anthropic.claude"}


@pytest.mark.parametrize(
    "endpoint", ["https://gateway.example/v1", "https://api.openai.com.evil.test/v1", "not-a-url", "https://["]
)
def test_compatible_or_invalid_endpoint_does_not_imply_billing_provider(endpoint):
    tags = route_tags({"model": "openai/gpt-4o", "api_base": endpoint})
    assert tags["ai.route.model"] == "openai/gpt-4o"
    assert "ai.billing.provider" not in tags


def test_endpoint_exports_host_only_and_effective_endpoint_replaces_default():
    tags = route_tags(
        {"model": "openai/gpt-4o", "api_base": "https://user:PRIVATE@api.openai.com/PRIVATE?key=PRIVATE#PRIVATE"}
    )
    assert tags["ai.route.endpoint_host"] == "api.openai.com"
    assert "PRIVATE" not in repr(tags)
    tags = route_tags({"api_base": "https://gateway.example/v1"}, tags)
    assert "ai.billing.provider" not in tags
    assert tags["ai.route.model"] == "openai/gpt-4o"


def test_raw_pricing_values_and_bounded_cache_scan():
    data = {
        "service_tier": "auto",
        "speed": "fast",
        "reasoning": {"effort": "high"},
        "dimensions": 256,
        "n": True,
        "quality": "future-quality",
        "messages": [
            {"content": [{"text": "PRIVATE", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]},
        ],
        "system": [{"cache_control": {"type": "ephemeral"}, "text": "PRIVATE"}],
        "api_key": "sk-PRIVATE",
        "metadata": {"cache_control": {"type": "ephemeral", "ttl": "PRIVATE"}},
    }
    tags = request_tags(data, "ai.request")
    tags.update(cache_tags(data, "ai.request"))
    assert tags == {
        "ai.request.service_tier": "auto",
        "ai.request.speed": "fast",
        "ai.request.reasoning_effort": "high",
        "ai.request.dimensions": "256",
        "ai.request.prompt_cache_ttls": "1h",
        "ai.request.prompt_cache_types": "ephemeral",
        "ai.request.prompt_cache_ttl_unspecified": "true",
        "ai.request.quality": "future-quality",
    }
    assert cache_tags({"content": [{"cachePoint": {"type": "default", "ttl": "1h"}}]}, "x") == {
        "x.prompt_cache_ttls": "1h",
        "x.prompt_cache_types": "default",
    }
    data["messages"] = [data] * 1000
    assert cache_tags(data, "x")["x.prompt_cache_scan"] == "incomplete"


async def test_effective_settings_do_not_reuse_ingress_or_previous_route_settings():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, data={"service_tier": "priority", "cache_control": {"type": "ephemeral", "ttl": "1h"}})
    await callback.async_pre_call_deployment_hook(
        {**data, "model": "anthropic/claude", "model_info": {"id": "first"}}, "completion"
    )
    callback.log_pre_api_call(
        None,
        "PRIVATE",
        {
            "litellm_params": data,
            "additional_args": {
                "complete_input_dict": {"service_tier": "flex", "cache_control": {"type": "ephemeral"}}
            },
        },
    )
    await callback.async_pre_call_deployment_hook(
        {**data, "model": "openai/gpt-4o", "model_info": {"id": "dep-1"}}, "completion"
    )
    callback.log_pre_api_call(
        None,
        "PRIVATE",
        {
            "litellm_params": data,
            "additional_args": {"complete_input_dict": {"service_tier": "auto", "reasoning_effort": "high"}},
        },
    )
    await finish(callback, data, response(Usage(prompt_tokens=10, completion_tokens=2)))
    tags = records[0].tags
    assert tags["ai.route.model"] == "openai/gpt-4o"
    assert tags["ai.request.prompt_cache_ttls"] == "1h"
    assert "ai.effective.prompt_cache_ttls" not in tags
    assert tags["ai.effective.service_tier"] == "auto"
    assert tags["ai.effective.reasoning_effort"] == "high"
    assert "ai.billing.mode" not in tags
    assert "PRIVATE" not in repr(records)


def test_multimodal_counters_survive_ambiguous_partition():
    result = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {
                "text_tokens": 70,
                "audio_tokens": 20,
                "image_tokens": 10,
                "cached_tokens": 5,
                "cache_write_tokens": 10,
                "cache_creation_token_details": {"ephemeral_1h_input_tokens": 10},
                "audio_length_seconds": 2.5,
                "video_length_seconds": 1.25,
                "image_count": 2,
                "character_count": 500,
                "tool_use_tokens": 12,
                "google_maps_grounding_requests": 1,
            },
            "completion_tokens_details": {"audio_tokens": 20, "reasoning_tokens": 5},
            "server_tool_use": {"web_search_requests": 2, "browser_open_requests": 3},
        }
    )
    assert result.quantities == {}
    assert result.issues == {"multimodal_partition_unsupported"}
    for key, expected in {
        "input_text_tokens": 70,
        "input_audio_tokens": 20,
        "input_image_tokens": 10,
        "input_cache_read_tokens": 5,
        "input_cache_write_tokens": 10,
        "input_cache_write_1h_tokens": 10,
        "input_audio_length_seconds": 2.5,
        "input_video_length_seconds": 1.25,
        "input_image_count": 2,
        "input_character_count": 500,
        "input_tool_use_tokens": 12,
        "output_audio_tokens": 20,
        "output_reasoning_tokens": 5,
        "web_search_requests": 2,
        "browser_open_requests": 3,
        "google_maps_grounding_requests": 1,
    }.items():
        assert result.diagnostics[key] == expected


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1, True, "2.5"])
def test_invalid_modality_duration_is_not_exported(duration):
    result = normalize_usage(
        {"prompt_tokens": 1, "completion_tokens": 1, "prompt_tokens_details": {"audio_length_seconds": duration}}
    )
    assert result.issues == {"invalid_usage"}
    assert "input_audio_length_seconds" not in result.diagnostics


def test_prompt_only_embeddings_and_tool_counts_without_double_counting():
    result = normalize_usage({"prompt_tokens": 123}, "aembedding")
    assert result.quantities["input_uncached_tokens"] == 123
    assert "output_tokens" not in result.quantities
    assert not result.issues
    result = normalize_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "server_tool_use": {"web_search_requests": 2},
            "prompt_tokens_details": {"web_search_requests": 2},
        }
    )
    assert result.quantities["web_search_requests"] == 2
    assert result.diagnostics["web_search_requests"] == 2
    result = normalize_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "server_tool_use": {"web_search_requests": 2},
            "prompt_tokens_details": {"web_search_requests": 3},
        }
    )
    assert "conflicting_tool_usage" in result.issues
    assert "web_search_requests" not in result.quantities


def test_native_provider_pricing_settings_are_not_lost_in_translation():
    tags = request_tags(
        {
            "serviceTier": {"type": "flex"},
            "performanceConfig": {"latency": "optimized"},
            "generationConfig": {"thinkingConfig": {"thinkingBudget": 1024}, "maxOutputTokens": 2048},
            "web_search_options": {"search_context_size": "high", "user_location": "PRIVATE"},
        },
        "ai.effective",
    )
    assert tags == {
        "ai.effective.service_tier": "flex",
        "ai.effective.performance_latency": "optimized",
        "ai.effective.thinking_budget_tokens": "1024",
        "ai.effective.max_output_tokens": "2048",
        "ai.effective.web_search_context_size": "high",
    }


async def test_response_route_mismatch_drops_stale_automatic_dimensions():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await callback.async_pre_call_deployment_hook(
        {**data, "model": "openai/gpt-4o", "organization": "org-stale", "model_info": {"id": "first"}}, "completion"
    )
    await finish(callback, data, response(Usage(prompt_tokens=1, completion_tokens=1), deployment="different"))
    assert "ai.route.organization" not in records[0].tags
    assert "ai.route.model" not in records[0].tags
    assert "selected_route_metadata_mismatch" in records[0].tags["ai.attribution.issues"]


async def test_new_metadata_hooks_never_fail_the_request(monkeypatch, caplog):
    def fail(*args):
        raise RuntimeError("PRIVATE")

    callback = make_callback()
    data = await start(callback)
    monkeypatch.setattr("ddtrace.contrib.internal.litellm.gateway.route_tags", fail)
    await callback.async_pre_call_deployment_hook(data, "completion")
    callback.log_pre_api_call(None, None, data)
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize(
    "field,value", [("image_count", 2), ("audio_length_seconds", 2.5), ("video_length_seconds", 1.25)]
)
def test_non_token_modality_units_do_not_imply_text_only_usage(field, value):
    result = normalize_usage({"prompt_tokens": 10, "prompt_tokens_details": {field: value}}, "aembedding")
    assert result.quantities == {}
    assert result.diagnostics[f"input_{field}"] == value
    assert result.issues == {"multimodal_partition_unsupported"}


def test_callback_disables_both_legacy_and_current_message_logging():
    callback = make_callback()
    assert callback.message_logging is False
    assert callback.turn_off_message_logging is True


@pytest.mark.parametrize("parsed", [False, True])
@pytest.mark.parametrize("sdk_options", [False, True])
async def test_outgoing_endpoint_and_scope_override_route_defaults_and_reach_record(parsed, sdk_options):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, data={"headers": {"OpenAI-Project": "spoofed"}, "project": "spoofed"})
    await callback.async_pre_call_deployment_hook(
        {**data, "model": "openai/gpt-4o", "organization": "org-default", "model_info": {"id": "dep-1"}}, "completion"
    )
    url = httpx.URL("https://PRIVATE:PRIVATE@eu.api.openai.com/PRIVATE?key=PRIVATE#PRIVATE")
    headers = {
        "OpenAI-Organization": "org-outgoing",
        "oPeNaI-pRoJeCt": "proj-outgoing",
        "Authorization": "Bearer PRIVATE",
        "X-User-Email": "PRIVATE",
    }
    callback.log_pre_api_call(
        None,
        "PRIVATE",
        {
            "litellm_params": data,
            "additional_args": {
                "api_base": url._uri_reference if parsed else str(url),
                "headers": {"Authorization": "Bearer PRIVATE"} if sdk_options else headers,
                "complete_input_dict": {"extra_headers": headers} if sdk_options else {},
            },
        },
    )
    await finish(callback, data, response(Usage(prompt_tokens=200001, completion_tokens=2)))
    record = records[0]
    assert record.tags.get("ai.route.endpoint_host") == "eu.api.openai.com"
    assert record.tags.get("ai.route.organization") == "org-outgoing"
    assert record.tags.get("ai.route.project") == "proj-outgoing"
    assert record.usage.diagnostics["context_tokens"] == 200001
    assert "PRIVATE" not in repr(record)
    assert "spoofed" not in repr(record)


@pytest.mark.parametrize(
    "headers",
    [
        {"OpenAI-Project": "sk-secret", "OpenAI-Organization": "Bearer secret"},
        {"OpenAI-Project": ["secret"], "OpenAI-Organization": "org-\nsecret"},
        {
            "OpenAI-Project": "proj-1",
            "openai-project": "proj-2",
            "OpenAI-Organization": "org-1",
            "openai-organization": "org-2",
        },
        {str(i): "secret" for i in range(129)},
    ],
)
def test_invalid_or_ambiguous_outgoing_scope_does_not_reuse_defaults(headers):
    tags = route_tags(
        {"model": "openai/gpt-4o", "organization": "org-default"},
        {"ai.route.project": "proj-default"},
        headers=headers,
    )
    assert "ai.route.organization" not in tags
    assert "ai.route.project" not in tags
    assert "secret" not in repr(tags)


def test_openai_scope_headers_are_not_billing_scope_on_custom_or_other_provider_endpoints():
    for route in (
        {"model": "openai/gpt-4o", "api_base": "https://gateway.example/v1"},
        {"model": "azure/gpt-4o"},
    ):
        tags = route_tags(route, headers={"OpenAI-Organization": "org-1", "OpenAI-Project": "proj-1"})
        assert tags["ai.route.organization"] == "org-1"
        assert tags["ai.route.project"] == "proj-1"
        assert not any(key.startswith("ai.billing.") for key in tags)


def test_arbitrary_endpoint_objects_are_not_stringified():
    class Endpoint:
        scheme = "https"
        host = "us.api.openai.com"

        def __str__(self):
            raise AssertionError("Do not stringify URLs containing secrets")

    assert (
        route_tags({"model": "openai/gpt-4o", "api_base": Endpoint()})["ai.route.endpoint_host"] == "us.api.openai.com"
    )


@pytest.mark.parametrize("resource_type", ["application-inference-profile", "future-resource-type"])
@pytest.mark.parametrize("in_model", [False, True])
async def test_bedrock_identifiers_reach_record_without_parsing(in_model, resource_type):
    arn = f"arn:aws:bedrock:us-east-1:123456789012:{resource_type}/profile-1"
    route = {"model": "bedrock/anthropic.claude", "model_id": arn}
    if in_model:
        route = {"model": "bedrock/" + arn}
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, data={"metadata": {"model_id": "spoofed"}})
    await callback.async_pre_call_deployment_hook({**data, **route, "model_info": {"id": "dep-1"}}, "completion")
    callback.log_pre_api_call(None, None, {"litellm_params": data, "additional_args": {}})
    await finish(callback, data, response(Usage(prompt_tokens=10, completion_tokens=1)))
    record = records[0]
    assert record.tags.get("ai.route.model" if in_model else "ai.route.model_id") == (
        "bedrock/" + arn if in_model else arn
    )
    assert record.tags.get("ai.gateway.deployment_id") == "dep-1"
    assert record.tags.get("ai.billing.account_id") is None
    assert record.tags.get("ai.billing.geography") is None
    assert "spoofed" not in repr(record)


def test_provider_model_id_overrides_bedrock_profile_and_is_not_a_gateway_deployment():
    arn = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/profile-1"
    previous = route_tags({"model": "bedrock/anthropic.claude", "model_id": arn})
    tags = route_tags({"model_id": "anthropic.claude-v2"}, previous)
    assert tags["ai.route.model_id"] == "anthropic.claude-v2"
    assert "ai.billing.resource_id" not in tags
    tags = route_tags({"model": "openai/gpt-4o", "model_id": arn})
    assert tags["ai.route.model_id"] == arn
    assert "ai.billing.resource_id" not in tags
    tags = route_tags({"api_base": "https://gateway.example"}, previous)
    assert tags["ai.route.model_id"] == arn
    assert "ai.billing.resource_id" not in tags


@pytest.mark.parametrize(
    "host",
    [
        "resource.services.ai.azure.com",
        "future.api.openai.com",
        "custom-gateway.example",
        "aiplatform.googleapis.com.evil.test",
    ],
)
def test_endpoint_host_is_preserved_without_billing_inference(host):
    tags = route_tags({"custom_llm_provider": "Future_Provider", "api_base": f"https://{host}/path"})
    assert tags == {"ai.route.provider": "Future_Provider", "ai.route.endpoint_host": host}


def test_oci_explicit_scope_and_credentials_are_not_mixed():
    tags = route_tags(
        {
            "model": "oci/cohere.command-r-plus",
            "oci_tenancy": "ocid1.tenancy.oc1..test",
            "oci_compartment_id": "ocid1.compartment.oc1..test",
            "oci_region": "us-ashburn-1",
            "oci_key": "PRIVATE key",
            "oci_key_file": "/PRIVATE.pem",
            "oci_user": "PRIVATE user",
            "oci_fingerprint": "PRIVATE fingerprint",
        }
    )
    assert tags["ai.route.oci_tenancy"] == "ocid1.tenancy.oc1..test"
    assert "ai.billing.account_id" not in tags
    assert tags["ai.route.oci_compartment_id"] == "ocid1.compartment.oc1..test"
    assert tags["ai.route.oci_region"] == "us-ashburn-1"
    assert "PRIVATE" not in repr(tags)
    resolved = route_tags({"api_base": "https://gateway.example"}, tags)
    assert resolved["ai.route.oci_compartment_id"] == "ocid1.compartment.oc1..test"
    assert resolved["ai.route.endpoint_host"] == "gateway.example"
    assert not any(key.startswith("ai.billing.") for key in resolved)


@pytest.mark.parametrize(
    "key", ["oci_key", "oci_key_file", "oci_user", "oci_fingerprint", "oci_tenancy", "oci_compartment_id"]
)
async def test_ingress_oci_fields_are_not_exported_as_route_evidence(key):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, data={key: "PRIVATE"})
    await finish(callback, data, response(Usage(prompt_tokens=3, completion_tokens=1)))
    assert f"ai.route.{key}" not in records[0].tags
    assert "PRIVATE" not in repr(records)


@pytest.mark.parametrize(
    "traffic",
    [
        "ON_DEMAND",
        "ON_DEMAND_PRIORITY",
        "ON_DEMAND_FLEX",
        "PROVISIONED_THROUGHPUT",
        "TRAFFIC_TYPE_UNSPECIFIED",
        "Future_Traffic",
    ],
)
async def test_raw_traffic_type_reaches_record_without_mapping(traffic):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, data={"metadata": {"traffic_type": "spoofed"}})
    await callback.async_pre_call_deployment_hook(
        {**data, "model": "vertex_ai/gemini-2.5-pro", "model_info": {"id": "dep-1"}}, "completion"
    )
    result = response(Usage(prompt_tokens=10, completion_tokens=1), service_tier="default")
    result._hidden_params["provider_specific_fields"] = {"traffic_type": traffic, "thought_signature": "PRIVATE"}
    await finish(callback, data, result)
    record = records[0]
    assert record.tags.get("ai.observed.traffic_type") == traffic
    assert record.tags.get("ai.observed.service_tier") == "default"
    assert record.tags.get("ai.billing.mode") is None
    assert "PRIVATE" not in repr(record)
    assert "spoofed" not in repr(record)


@pytest.mark.parametrize(
    "field", ["service_tier", "speed", "reasoning_effort", "quality", "size", "prompt_cache_retention", "inference_geo"]
)
def test_new_pricing_values_are_retained(field):
    assert request_tags({field: "Future_Value"}, "ai.effective") == {f"ai.effective.{field}": "Future_Value"}


def test_new_native_pricing_and_cache_values_are_retained():
    assert request_tags(
        {
            "reasoning": {"effort": "Future_Effort"},
            "serviceTier": {"type": "Future_Tier"},
            "web_search_options": {"search_context_size": "Future_Size"},
        },
        "x",
    ) == {
        "x.reasoning_effort": "Future_Effort",
        "x.service_tier": "Future_Tier",
        "x.web_search_context_size": "Future_Size",
    }
    for parent in ("performanceConfig", "performance_config"):
        assert request_tags({parent: {"latency": "Future_Latency"}}, "x") == {"x.performance_latency": "Future_Latency"}
    for control in ("cache_control", "cachePoint"):
        assert cache_tags({control: {"type": "Future_Type", "ttl": "2h"}}, "x") == {
            "x.prompt_cache_types": "Future_Type",
            "x.prompt_cache_ttls": "2h",
        }
    assert response_tags(
        {"service_tier": "auto", "usage": {"speed": "Future_Speed", "inference_geo": "Future_Geo"}}
    ) == {
        "ai.observed.service_tier": "auto",
        "ai.observed.speed": "Future_Speed",
        "ai.observed.inference_geo": "Future_Geo",
    }


def test_unique_response_request_ids_are_not_metric_tags():
    headers = dict.fromkeys(
        ("x-request-id", "request-id", "x-amzn-requestid", "apim-request-id", "opc-request-id"), "unique-id"
    )
    assert not response_tags({"id": "response-id", "_hidden_params": {"additional_headers": headers}})
    assert not response_tags({"_hidden_params": {"additional_headers": dict.fromkeys(map(str, range(129)))}})


@pytest.mark.parametrize(
    "header",
    ("openai-organization", "openai-project", "anthropic-organization-id", "anthropic-workspace-id"),
)
def test_provider_scope_response_headers(header):
    tag = f"ai.response.{header.replace('-', '_')}"
    headers = {f"llm_provider-{header.upper()}": "scope-from-provider", header: "scope-from-provider"}
    assert response_tags({"_hidden_params": {"additional_headers": headers}}) == {tag: "scope-from-provider"}
    for invalid in ("different-scope", "sk-PRIVATE", "Bearer PRIVATE", "x" * 257, "scope\nPRIVATE", {}, None):
        headers[header] = invalid
        assert not response_tags({"_hidden_params": {"additional_headers": headers}})
        if invalid != "different-scope":
            assert not response_tags({"_hidden_params": {"additional_headers": {header: invalid}}})
    # Request headers and arbitrary response metadata must not supply provider identity.
    assert not response_tags({"headers": {header: "spoofed"}, "metadata": {header: "spoofed"}})
    assert not response_tags({}, provider_response={"headers": {header: "spoofed"}})


async def test_provider_http_response_headers_without_reading_stream():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)

    async def unread_stream():
        pytest.fail("Provider response body must not be consumed")
        yield b"PRIVATE"

    raw = httpx.Response(
        200,
        headers={"anthropic-workspace-id": "workspace-stream", "set-cookie": "PRIVATE"},
        content=unread_stream(),
    )
    await finish(callback, data, stream=True, httpx_response=raw)
    assert records[0].tags["ai.response.anthropic_workspace_id"] == "workspace-stream"
    assert "PRIVATE" not in repr(records)
    assert not raw.is_stream_consumed
    await raw.aclose()
    # Two representations of the provider headers must agree.
    assert not response_tags(
        {"_hidden_params": {"additional_headers": {"llm_provider-anthropic-workspace-id": "other"}}},
        provider_response=raw,
    )


@pytest.mark.parametrize(
    "final_key_id", ["key_final", "apikey_final", None, "", " ", "sk-PRIVATE", "Bearer PRIVATE", {}, 123]
)
async def test_provider_key_id_comes_from_selected_deployment(final_key_id, caplog, monkeypatch):
    monkeypatch.setattr(internal_logger, "_rate_limit", 0)
    caplog.set_level(logging.WARNING, logger="ddtrace.contrib.internal.litellm.gateway")
    records = []
    callback = make_callback(sink=records.append)
    data = await start(
        callback,
        data={"model_info": {"datadog_provider_api_key_id": "spoofed"}, "api_key_id": "spoofed"},
    )
    await callback.async_pre_call_deployment_hook(
        {**data, "model_info": {"id": "failed", "datadog_provider_api_key_id": "key_failed"}}, "completion"
    )
    await callback.async_pre_call_deployment_hook(
        {**data, "model_info": {"id": "dep-1", "datadog_provider_api_key_id": final_key_id}}, "completion"
    )
    # The lower-level hook must preserve the configured ID, not take an ID from request kwargs.
    callback.log_pre_api_call("model", [], {"litellm_params": data, "api_key_id": "spoofed"})
    await finish(callback, data)
    expected = final_key_id if final_key_id in ("key_final", "apikey_final") else None
    assert records[0].tags.get("ai.route.api_key_id") == expected
    assert "spoofed" not in repr(records)
    assert "key_failed" not in repr(records)
    assert ("model_info.datadog_provider_api_key_id" in caplog.text) is (expected is None)
    assert "PRIVATE" not in caplog.text
    assert "spoofed" not in caplog.text


async def test_missing_provider_key_warnings_are_rate_limited(caplog, monkeypatch):
    monkeypatch.setattr(
        internal_logger, "_buckets", defaultdict(lambda: internal_logger.LoggingBucket(float("-inf"), 0))
    )
    monkeypatch.setattr(internal_logger, "_rate_limit", 60)
    caplog.set_level(logging.WARNING, logger="ddtrace.contrib.internal.litellm.gateway")
    records = []
    callback = make_callback(sink=records.append)
    for _ in range(3):
        data = await start(callback)
        await finish(callback, data)
    assert len(records) == 3
    assert all("ai.route.api_key_id" not in record.tags for record in records)
    assert caplog.text.count("model_info.datadog_provider_api_key_id") == 1
    assert "non-secret key ID" in caplog.text
    assert "Usage is still collected" in caplog.text


@pytest.mark.parametrize("cache_hit", [True, None])
async def test_no_missing_provider_key_warning_for_gateway_cache_hits(cache_hit, caplog, monkeypatch):
    monkeypatch.setattr(internal_logger, "_rate_limit", 0)
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await finish(callback, data, cache_hit=cache_hit, standard_logging_object={"cache_hit": True})
    assert records[0].tags["ai.request.outcome"] == "gateway_cache_hit"
    assert "model_info.datadog_provider_api_key_id" not in caplog.text


async def test_untracked_sdk_call_does_not_warn_about_provider_key_id(caplog, monkeypatch):
    monkeypatch.setattr(internal_logger, "_rate_limit", 0)
    records = []
    callback = make_callback(sink=records.append)
    await finish(callback, {})
    assert not records
    assert "model_info.datadog_provider_api_key_id" not in caplog.text


@pytest.mark.parametrize("routed", [False, True])
async def test_provider_key_id_not_taken_from_ingress_or_mismatched_route(routed):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback, data={"model_info": {"datadog_provider_api_key_id": "spoofed"}})
    if routed:
        await callback.async_pre_call_deployment_hook(
            {**data, "model_info": {"id": "wrong-deployment", "datadog_provider_api_key_id": "key_wrong"}},
            "completion",
        )
    await finish(callback, data)
    assert "ai.route.api_key_id" not in records[0].tags


def test_long_resource_ids_remain_exact_with_a_separate_bound():
    resource = (
        "/subscriptions/sub/resourceGroups/"
        + "g" * 100
        + "/providers/Microsoft.CognitiveServices/accounts/"
        + "a" * 100
    )
    assert route_tags({"resource_id": resource})["ai.route.resource_id"] == resource
    assert len(resource) > 256
    for invalid in ("x" * 2049, "sk-PRIVATE", "resource\nPRIVATE", {"PRIVATE": "PRIVATE"}):
        assert not route_tags({"resource_id": invalid})


@pytest.mark.parametrize("sdk_usage", [False, True])
def test_absent_cache_details_remain_unknown_not_explicit_zero(sdk_usage):
    raw = {"prompt_tokens": 100, "completion_tokens": 10}
    usage = normalize_usage(Usage(**raw) if sdk_usage else raw)
    assert usage.diagnostics["input_cache_read_reported"] == 0
    assert usage.diagnostics["input_cache_write_reported"] == 0
    assert "input_cache_read_tokens" not in usage.diagnostics
    assert "input_cache_write_tokens" not in usage.diagnostics
    assert usage.quantities == {"output_tokens": 10}
    assert {"cache_read_detail_missing", "cache_write_detail_missing"} <= usage.issues
    raw.update(cache_read_input_tokens=0, cache_creation_input_tokens=0)
    usage = normalize_usage(Usage(**raw) if sdk_usage else raw)
    assert usage.diagnostics["input_cache_read_reported"] == 1
    assert usage.diagnostics["input_cache_write_reported"] == 1
    assert usage.quantities["input_uncached_tokens"] == 100
    assert not usage.issues


async def test_standard_logging_common_fields_without_exporting_its_content():
    records = []
    callback = make_callback(sink=records.append, capture_email=False)
    data = await start(callback, data={"standard_logging_object": {"model": "SPOOFED"}})
    assert "standard_logging_object" not in data
    standard = {
        "model": "future_provider/model-v2",
        "custom_llm_provider": "future_provider",
        "model_id": "deployment-new",
        "api_base": "https://PRIVATE:PRIVATE@provider.example/PRIVATE?key=PRIVATE",
        "stream": True,
        "cache_hit": False,
        "prompt_tokens": 999,
        "completion_tokens": 999,
        "messages": "PRIVATE",
        "response": "PRIVATE",
        "model_parameters": {"metadata": "PRIVATE", "api_key": "PRIVATE"},
        "metadata": {"user_api_key_user_id": "SPOOFED", "user_email": "PRIVATE"},
        "hidden_params": {"additional_headers": {"openai-project": "SPOOFED"}},
        "api_key": "PRIVATE",
        "vertex_credentials": "PRIVATE",
        "future_field": "PRIVATE",
    }
    await finish(
        callback,
        data,
        {"usage": {"prompt_tokens": 12, "completion_tokens": 3}},
        standard_logging_object=standard,
    )
    record = records[0]
    assert record.tags["ai.route.model"] == "future_provider/model-v2"
    assert record.tags["ai.route.provider"] == "future_provider"
    assert "ai.model.provider" not in record.tags  # Do not copy the same standard field into a second tag.
    assert record.tags["ai.route.endpoint_host"] == "provider.example"
    assert record.tags["ai.gateway.deployment_id"] == "deployment-new"
    assert record.tags["ai.model"] == "future_provider/model-v2"
    assert record.tags["usr.id"] == "user-1"
    assert record.tags["ai.usage.source"] == "litellm_normalized_may_estimate"
    assert record.usage.diagnostics["input_tokens"] == 12
    assert record.usage.quantities["output_tokens"] == 3
    assert "ai.route.model_id" not in record.tags  # Logging model_id is a deployment, not a provider resource.
    assert not any(value in repr(record) for value in ("PRIVATE", "SPOOFED", "999"))


@pytest.mark.parametrize("standard", [None, [], "PRIVATE", {"model": [], "api_base": 123, "model_id": True}])
async def test_missing_or_malformed_standard_logging_keeps_legacy_collection(standard):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await finish(callback, data, standard_logging_object=standard)
    assert records[0].tags["ai.model"] == "gpt-4o-2024-08-06"
    assert records[0].tags["ai.gateway.deployment_id"] == "dep-1"
    assert "unsupported_callback_shape" not in records[0].tags.get("ai.attribution.issues", "")
    assert "PRIVATE" not in repr(records)


async def test_standard_logging_does_not_turn_missing_usage_into_zero():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await finish(
        callback,
        data,
        {},
        standard_logging_object={
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "metadata": {"usage_object": {"prompt_tokens": 0, "completion_tokens": 0}},
        },
    )
    assert records[0].usage.quantities == {}
    assert "missing_usage" in records[0].tags["ai.attribution.issues"]


@pytest.mark.parametrize("legacy,standard,hit", [(None, True, True), (True, False, True), (False, True, False)])
async def test_standard_logging_cache_status_is_a_compatibility_fallback(legacy, standard, hit):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await finish(callback, data, cache_hit=legacy, standard_logging_object={"cache_hit": standard})
    assert records[0].tags["ai.request.outcome"] == ("gateway_cache_hit" if hit else "success")


async def test_standard_logging_does_not_replace_outgoing_settings_or_raw_response():
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await callback.async_pre_call_deployment_hook(
        {
            **data,
            "model": "openai/routed-model",
            "api_base": "https://outgoing.example/v1",
            "model_info": {"id": "dep-1", "datadog_provider_api_key_id": "key_selected"},
        },
        "completion",
    )
    await finish(
        callback,
        data,
        standard_logging_object={
            "model": "logging-alias",
            "model_id": "dep-1",
            "api_base": "https://default.example/v1",
            "custom_llm_provider": "openai",
        },
    )
    tags = records[0].tags
    assert tags["ai.route.model"] == "openai/routed-model"
    assert tags["ai.route.endpoint_host"] == "outgoing.example"
    assert tags["ai.route.provider"] == "openai"  # Filled from the common payload.
    assert tags["ai.route.api_key_id"] == "key_selected"
    assert tags["ai.model"] == "gpt-4o-2024-08-06"


@pytest.mark.parametrize("response_deployment", [None, "dep-new"])
async def test_standard_logging_deployment_mismatch_does_not_reuse_old_scope(response_deployment):
    records = []
    callback = make_callback(sink=records.append)
    data = await start(callback)
    await callback.async_pre_call_deployment_hook(
        {
            **data,
            "model": "old-model",
            "organization": "old-org",
            "model_info": {"id": "dep-old", "datadog_provider_api_key_id": "key_old"},
        },
        "completion",
    )
    await finish(
        callback,
        data,
        response(deployment=response_deployment),
        standard_logging_object={
            "model_id": "dep-standard",
            "model": "standard-model",
            "custom_llm_provider": "future_provider",
        },
    )
    tags = records[0].tags
    assert "selected_route_metadata_mismatch" in tags["ai.attribution.issues"]
    assert "ai.route.api_key_id" not in tags
    assert "ai.route.organization" not in tags
    if response_deployment:
        assert "standard_logging_metadata_mismatch" in tags["ai.attribution.issues"]
        assert "ai.route.provider" not in tags
    else:
        assert tags["ai.route.provider"] == "future_provider"
    assert tags["ai.gateway.deployment_id"] == (response_deployment or "dep-standard")


def test_sink_initializes_lazily_and_recreates_client_after_fork(monkeypatch):
    from ddtrace.contrib.internal.litellm import _gateway_usage

    parent, child = Mock(spec=DogStatsd), Mock(spec=DogStatsd)
    factory = Mock(return_value=parent)
    monkeypatch.setattr(_gateway_usage, "get_dogstatsd_client", factory)
    monkeypatch.setattr(_gateway_usage.os, "getpid", lambda: 10)
    sink = DatadogSink()
    factory.assert_not_called()
    record = UsageRecord({}, RecordedUsage())
    sink(record)
    factory.assert_called_once()
    factory.return_value = child
    monkeypatch.setattr(_gateway_usage.os, "getpid", lambda: 11)
    sink(record)
    assert factory.call_count == 2
    parent.increment.assert_called_once()
    child.increment.assert_called_once()
    sink.close()
    child.close_socket.assert_called_once()
    parent.close_socket.assert_not_called()


async def test_invalid_metrics_endpoint_never_breaks_gateway(monkeypatch):
    from ddtrace.contrib.internal.litellm import _gateway_usage

    monkeypatch.setattr(_gateway_usage, "get_dogstatsd_client", Mock(side_effect=ValueError("PRIVATE")))
    callback = GatewayAttribution()
    await finish(callback, await start(callback))
    assert not callback._pending


@pytest.mark.parametrize("transport", ["udp", "unix"])
def test_real_dogstatsd_wire_preserves_fractions_and_escapes_tag_delimiters(tmp_path, transport):
    family = socket.AF_INET if transport == "udp" else socket.AF_UNIX
    with socket.socket(family, socket.SOCK_DGRAM) as receiver:
        receiver.settimeout(2)
        if transport == "udp":
            receiver.bind(("127.0.0.1", 0))
            client = DogStatsd(host="127.0.0.1", port=receiver.getsockname()[1], disable_telemetry=True)
        else:
            path = str(tmp_path / "dogstatsd.sock")
            receiver.bind(path)
            client = DogStatsd(socket_path=path, disable_telemetry=True)
        sink = DatadogSink(client)
        try:
            sink(
                UsageRecord(
                    {"usr.id": "alice|#forged:true,extra:true", "usr.email": "alice@example.test"},
                    RecordedUsage(diagnostics={"input_audio_length_seconds": 0.25}),
                )
            )
            lines = [receiver.recv(65535).decode() for _ in range(2)]
            assert lines[0].startswith("ai_gateway.observed.input_audio_length_seconds:0.25|c|#")
            assert lines[1].startswith("ai_gateway.requests:1|c|#")
            for line in lines:
                tags = line.split("|#", 1)[1].split("|", 1)[0].split(",")
                assert "usr.email:alice_example.test" in tags
                assert "usr.id:alice__forged:true_extra:true" in tags
                assert not any(t.startswith(("forged:", "extra:")) for t in tags)
        finally:
            sink.close()
