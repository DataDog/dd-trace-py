import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
import time
from types import SimpleNamespace

import httpx
from litellm import ModelResponse
from litellm import Usage
import pytest

from ddtrace.contrib.internal.litellm._gateway_metadata import cache_tags
from ddtrace.contrib.internal.litellm._gateway_metadata import request_tags
from ddtrace.contrib.internal.litellm._gateway_metadata import response_tags
from ddtrace.contrib.internal.litellm._gateway_metadata import route_tags
from ddtrace.contrib.internal.litellm._gateway_usage import BillingScope
from ddtrace.contrib.internal.litellm._gateway_usage import DatadogSink
from ddtrace.contrib.internal.litellm._gateway_usage import UsageRecord
from ddtrace.contrib.internal.litellm._gateway_usage import normalize_usage
from ddtrace.contrib.internal.litellm.gateway import CORRELATION_FIELD
from ddtrace.contrib.internal.litellm.gateway import GatewayAttribution
from ddtrace.contrib.internal.litellm.gateway import configured_callback


SCOPE = BillingScope(
    "openai",
    "org-1",
    "api",
    project_id="proj-1",
    api_key_id="key-id-1",
    geography="global",
    mode="standard",
)


def make_callback(routes=None, *, sink=None, max_pending=10000, pending_ttl=3600, **kwargs):
    callback = GatewayAttribution({key: vars(value) for key, value in (routes or {}).items()}, **kwargs)
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


async def test_identity_is_authenticated_no_secrets_and_opt_in_email():
    records = []
    callback = make_callback({"dep-1": SCOPE}, sink=records.append, auth_metadata_keys=("cost_center",))
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
    assert record.tags["ai.billing.api_key_id"] == "key-id-1"
    assert record.tags["ai.attribution.status"] == "observed"
    assert not any(word in repr(record) for word in ("SECRET", "sk-do-not-log", "victim", "@"))
    callback = make_callback({}, sink=records.append, capture_email=True)
    data = await start(callback, user_email="real@example.test")
    await finish(callback, data)
    assert records[-1].tags["usr.email"] == "real@example.test"


async def test_unknown_scope_byok_and_response_selected_deployment():
    records = []
    alternate = BillingScope("aws", "123456789012", "bedrock", geography="us-east-1", mode="on-demand")
    callback = make_callback({"dep-1": SCOPE, "fallback": alternate}, sink=records.append)
    data = await start(callback, data={"metadata": {"model_info": {"id": "dep-1"}}})
    await finish(callback, data, response(Usage(prompt_tokens=3, completion_tokens=1), "fallback"))
    assert records[-1].tags["ai.billing.provider"] == "aws"
    for data, deployment in [({}, "unknown"), ({"api_key": "sk-client"}, "dep-1")]:
        data = await start(callback, data=data)
        await finish(callback, data, response(deployment=deployment))
        assert "ai.billing.account_id" not in records[-1].tags
        assert "billing_scope_unknown" in records[-1].tags["ai.attribution.issues"]


async def test_stream_final_only_duplicates_and_cached_response():
    records = []
    callback = make_callback({"dep-1": SCOPE}, sink=records.append)
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


async def test_concurrent_users_failure_and_missing_identity():
    records = []
    callback = make_callback({}, sink=records.append)

    async def request(index):
        data = await start(callback, user=str(index))
        await asyncio.sleep(0)
        await finish(callback, data, response(Usage(prompt_tokens=index, completion_tokens=0)))

    await asyncio.gather(*(request(i) for i in range(30)))
    assert len(records) == 30
    assert all(int(r.tags["usr.id"]) == r.usage.diagnostics["input_tokens"] for r in records)
    data = await start(callback, user=None)
    await callback.async_post_call_failure_hook(data, Exception("sk-secret PROMPT"), None)
    assert records[-1].error
    assert "usr.id" not in records[-1].tags
    assert "sk-secret" not in repr(records)
    assert not callback._pending


def test_threaded_hooks_keep_user_usage_and_billing_scope_together():
    records = []
    scopes = {
        "dep-0": SCOPE,
        "dep-1": BillingScope("openai", "org-2", "api", project_id="proj-2", api_key_id="key-id-2"),
    }
    callback = make_callback(scopes, sink=records.append)

    def ingress(index):
        return asyncio.run(start(callback, user=str(index)))

    def route(item):
        index, data = item
        asyncio.run(
            callback.async_pre_call_deployment_hook({**data, "model_info": {"id": f"dep-{index % 2}"}}, "completion")
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
    assert len({record.tags["ai.request.id"] for record in records}) == 200
    assert {record.tags["usr.id"] for record in records} == {str(index) for index in range(200)}
    for record in records:
        index = int(record.tags["usr.id"])
        scope = scopes[f"dep-{index % 2}"]
        assert record.tags["ai.gateway.deployment_id"] == f"dep-{index % 2}"
        assert all(record.tags[key] == value for key, value in scope.tags().items())
        assert record.usage.quantities["input_uncached_tokens"] == index + 1
        assert record.usage.quantities["output_tokens"] == 2
        assert record.usage.diagnostics["attempts"] == 1
    assert not callback._pending


async def test_bounded_state_expiration_shutdown_and_broken_exporter():
    records = []
    callback = make_callback({}, sink=records.append, max_pending=1, pending_ttl=0.001)
    await start(callback)
    await asyncio.sleep(0.01)
    await start(callback)
    await start(callback)
    assert len(records) == 2
    callback.close()
    assert len(records) == 3
    assert not callback._pending
    callback = make_callback({}, sink=lambda _: 1 / 0)
    await finish(callback, await start(callback))  # Never turns a successful request into a 500.


@pytest.mark.parametrize(
    "kwargs",
    [
        {"auth_metadata_keys": ("api_key",)},
        {"auth_metadata_keys": ("a.b",)},
        {"capture_email": "false"},
        {"auth_metadata_keys": "cost_center"},
    ],
)
def test_bad_configuration(kwargs):
    with pytest.raises(ValueError):
        make_callback({}, **kwargs)


def test_real_tracer_span_api(tracer):
    test_tracer = tracer
    captured = []
    original = test_tracer.start_span

    def capture(*args, **kwargs):
        span = original(*args, **kwargs)
        captured.append(span)
        return span

    sink = DatadogSink(SimpleNamespace(start_span=capture))
    now = time.time()
    sink(
        UsageRecord(
            now - 2,
            now,
            {"usr.id": "u"},
            normalize_usage(
                {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                }
            ),
        )
    )
    span = captured[0]
    assert span.finished and span.duration == pytest.approx(2)
    assert span.get_tag("usr.id") == "u"
    assert span.get_metric("ai.usage.input_uncached_tokens") == 4


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
    callback = make_callback({"dep-1": SCOPE}, sink=records.append)
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


async def test_requested_tier_is_not_billed_tier_and_model_suffix_is_preserved():
    records = []
    scope = BillingScope("openai", "org", "api")
    callback = make_callback({"dep-1": scope}, sink=records.append)
    data = await start(callback, data={"service_tier": "priority"})
    await finish(callback, data, response(Usage(prompt_tokens=1, completion_tokens=0)))
    assert "ai.billing.mode" not in records[0].tags
    assert records[0].tags["ai.model"] == "gpt-4o-2024-08-06"
    data = await start(callback)
    await finish(callback, data, response(Usage(prompt_tokens=1, completion_tokens=0), service_tier="flex"))
    assert records[-1].tags["ai.billing.mode"] == "flex"


async def test_unknown_sdk_callbacks_and_conflicting_tokens_do_not_export():
    records = []
    callback = make_callback({}, sink=records.append)
    await finish(callback, {"metadata": {CORRELATION_FIELD: "client-invented"}})
    data = await start(callback)
    data["litellm_metadata"][CORRELATION_FIELD] = "mismatched"
    await finish(callback, data)
    assert records == []
    callback.close()
    assert len(records) == 1


@pytest.mark.parametrize(
    "value", ["sk-secret", "Bearer secret", " sk-secret", "secret\x7f", "", "x" * 257, "id\nheader"]
)
def test_billing_scope_rejects_bad_or_secret_like_ids(value):
    with pytest.raises(ValueError):
        BillingScope("openai", value, "api")


@pytest.mark.parametrize(
    "config", [[], "invalid", {"billing_scopes": []}, {"capture_email": "true"}, {"unexpected": 1}]
)
def test_invalid_file_configuration_fails_closed(tmp_path, monkeypatch, config):
    import json
    from unittest.mock import patch

    path = tmp_path / "attribution.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG", str(path))
    with patch("ddtrace.contrib.internal.litellm.gateway.log.warning") as warning:
        callback = configured_callback()
    warning.assert_called_once_with(
        "Invalid gateway attribution configuration; billing mappings and optional identity enrichment disabled"
    )
    assert not callback._routes
    assert not callback._capture_email
    assert not callback._auth_metadata_keys


def test_missing_configuration_disables_operator_mappings(monkeypatch):
    monkeypatch.delenv("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG", raising=False)
    assert not configured_callback()._routes
    monkeypatch.setenv("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG", "/nonexistent/attribution-config.json")
    assert not configured_callback()._routes


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


async def test_selected_route_attempts_and_parent_context(tracer, monkeypatch):
    records = []
    callback = make_callback({"dep-1": SCOPE}, sink=records.append)
    monkeypatch.setattr("ddtrace.contrib.internal.litellm.gateway.tracer", tracer)
    with tracer.trace("gateway.request") as parent:
        data = await start(callback)
        await callback.async_pre_call_deployment_hook({**data, "model_info": {"id": "failed"}}, "completion")
        await callback.async_pre_call_deployment_hook({**data, "model_info": {"id": "dep-1"}}, "completion")
    await finish(callback, data, response(Usage(prompt_tokens=4, completion_tokens=2), deployment=None))
    assert records[0].tags["ai.gateway.deployment_id"] == "dep-1"
    assert records[0].usage.diagnostics["attempts"] == 2
    assert "additional_attempt_usage_unknown" in records[0].tags["ai.attribution.issues"]
    assert records[0].parent.span_id == parent.span_id


@pytest.mark.parametrize(
    "bad_scope", [{"provider": "x"}, {"provider": "x", "account_id": "sk-secret", "product": "api"}]
)
def test_public_constructor_rejects_bad_billing_scope(bad_scope):
    with pytest.raises(ValueError, match="Invalid non-secret gateway billing fields"):
        GatewayAttribution({"deployment": bad_scope})


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
    "data,provider,product",
    [
        ({"model": "anthropic/claude-sonnet-4-20250514"}, "anthropic", "platform-api"),
        ({"model": "openai/gpt-4o", "organization": "org-real"}, "openai", "api"),
        ({"model": "bedrock/us.anthropic.claude", "aws_region_name": "us-east-1"}, "aws", "bedrock"),
        ({"custom_llm_provider": "vertex_ai", "vertex_project": "project-real"}, "gcp", "vertex-ai"),
        ({"custom_llm_provider": "azure", "api_base": "https://my-resource.openai.azure.com/"}, "azure", "foundry"),
        ({"custom_llm_provider": "gemini"}, "gcp", "gemini-api"),
    ],
)
def test_automatic_route_dimensions(data, provider, product):
    tags = route_tags({**data, "api_key": "sk-PRIVATE", "vertex_credentials": "PRIVATE credentials"})
    assert tags["ai.billing.provider"] == provider
    assert tags["ai.billing.product"] == product
    assert tags["ai.billing.provider_source"] == "selected_route"
    assert "ai.billing.geography" not in tags  # Routing region may differ from billed geography.
    assert "PRIVATE" not in repr(tags)
    if "organization" in data:
        assert tags["ai.billing.account_id"] == "org-real"
    if "vertex_project" in data:
        assert tags["ai.billing.project_id"] == "project-real"
        assert "ai.billing.account_id" not in tags  # Project is not the GCP billing account.


@pytest.mark.parametrize(
    "endpoint", ["https://gateway.example/v1", "https://api.openai.com.evil.test/v1", "not-a-url", "https://["]
)
def test_compatible_or_invalid_endpoint_does_not_imply_billing_provider(endpoint):
    tags = route_tags({"model": "openai/gpt-4o", "api_base": endpoint})
    assert tags["ai.route.provider"] == "openai"
    assert "ai.billing.provider" not in tags


def test_endpoint_exports_host_only_and_effective_endpoint_replaces_default():
    tags = route_tags(
        {"model": "openai/gpt-4o", "api_base": "https://user:PRIVATE@api.openai.com/PRIVATE?key=PRIVATE#PRIVATE"}
    )
    assert tags["ai.route.endpoint_host"] == "api.openai.com"
    assert "PRIVATE" not in repr(tags)
    tags = route_tags({"api_base": "https://gateway.example/v1"}, tags)
    assert "ai.billing.provider" not in tags
    assert tags["ai.route.provider"] == "openai"


def test_pricing_allowlist_and_bounded_cache_scan():
    data = {
        "service_tier": "auto",
        "speed": "fast",
        "reasoning": {"effort": "high"},
        "dimensions": 256,
        "n": True,
        "quality": "PRIVATE",
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
        "ai.request.prompt_cache_ttls": "1h,5m",
    }
    assert cache_tags({"content": [{"cachePoint": {"type": "default", "ttl": "1h"}}]}, "x") == {
        "x.prompt_cache_ttls": "1h"
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
    assert tags["ai.billing.provider"] == "openai"
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
    assert "ai.billing.account_id" not in records[0].tags
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


@pytest.mark.parametrize("host", ["aiplatform.googleapis.com", "us-central1-aiplatform.googleapis.com"])
def test_vertex_billing_provider_requires_a_complete_official_hostname(host):
    assert (
        route_tags({"custom_llm_provider": "vertex_ai", "api_base": f"https://{host}/v1"})["ai.billing.provider"]
        == "gcp"
    )


@pytest.mark.parametrize(
    "host",
    [
        "aiplatform.googleapis.com.evil.test",
        "evil.test-aiplatform.googleapis.com",
        "evil.aiplatform.googleapis.com",
        "aiplatformgoogleapis.com",
    ],
)
def test_vertex_lookalike_host_does_not_identify_billing_provider(host):
    assert "ai.billing.provider" not in route_tags(
        {"custom_llm_provider": "vertex_ai", "api_base": f"https://{host}/v1"}
    )


@pytest.mark.parametrize("region", ["us", "eu", "au", "ca", "jp", "in", "sg", "kr", "gb", "ae"])
def test_openai_regional_endpoint_preserves_scope_without_assuming_billing_geography(region):
    tags = route_tags({"model": "openai/gpt-4o", "api_base": f"https://{region}.api.openai.com/v1"})
    assert tags["ai.billing.provider"] == "openai"
    assert tags["ai.route.endpoint_region"] == region
    assert "ai.billing.geography" not in tags


@pytest.mark.parametrize(
    "host",
    ["us.api.openai.com.evil.test", "evil.us.api.openai.com", "unknown.api.openai.com", "us.api.openai.com@evil.test"],
)
def test_openai_regional_lookalike_is_not_an_official_endpoint(host):
    tags = route_tags({"model": "openai/gpt-4o", "api_base": f"https://{host}/v1"})
    assert "ai.billing.provider" not in tags
    assert "ai.route.endpoint_region" not in tags


@pytest.mark.parametrize("parsed", [False, True])
@pytest.mark.parametrize("sdk_options", [False, True])
async def test_outgoing_endpoint_and_scope_override_route_defaults_and_reach_apm(
    tracer, test_spans, parsed, sdk_options
):
    callback = make_callback(sink=DatadogSink(tracer))
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
    span = test_spans.pop()[0]
    assert span.get_tag("ai.route.endpoint_host") == "eu.api.openai.com"
    assert span.get_tag("ai.route.endpoint_region") == "eu"
    assert span.get_tag("ai.billing.provider") == "openai"
    assert span.get_tag("ai.billing.account_id") == "org-outgoing"
    assert span.get_tag("ai.billing.project_id") == "proj-outgoing"
    assert span.get_metric("ai.observed.context_tokens") == 200001
    assert "PRIVATE" not in repr(span)
    assert "spoofed" not in repr(span)


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
    assert "ai.billing.account_id" not in tags
    assert "ai.billing.project_id" not in tags
    assert "secret" not in repr(tags)


def test_openai_scope_headers_are_not_billing_scope_on_custom_or_other_provider_endpoints():
    for route in (
        {"model": "openai/gpt-4o", "api_base": "https://gateway.example/v1"},
        {"model": "azure/gpt-4o"},
    ):
        tags = route_tags(route, headers={"OpenAI-Organization": "org-1", "OpenAI-Project": "proj-1"})
        assert "ai.billing.account_id" not in tags
        assert "ai.billing.project_id" not in tags


def test_arbitrary_endpoint_objects_are_not_stringified():
    class Endpoint:
        scheme = "https"
        host = "us.api.openai.com"

        def __str__(self):
            raise AssertionError("Do not stringify URLs containing secrets")

    assert (
        route_tags({"model": "openai/gpt-4o", "api_base": Endpoint()})["ai.route.endpoint_host"] == "us.api.openai.com"
    )


@pytest.mark.parametrize("in_model", [False, True])
async def test_bedrock_resource_scope_reaches_apm_without_inventing_billed_account(tracer, test_spans, in_model):
    arn = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/profile-1"
    route = {"model": "bedrock/anthropic.claude", "model_id": arn}
    if in_model:
        route = {"model": "bedrock/" + arn}
    callback = make_callback(sink=DatadogSink(tracer))
    data = await start(callback, data={"metadata": {"model_id": "spoofed"}})
    await callback.async_pre_call_deployment_hook({**data, **route, "model_info": {"id": "dep-1"}}, "completion")
    callback.log_pre_api_call(None, None, {"litellm_params": data, "additional_args": {}})
    await finish(callback, data, response(Usage(prompt_tokens=10, completion_tokens=1)))
    span = test_spans.pop()[0]
    assert span.get_tag("ai.billing.resource_id") == arn
    assert span.get_tag("ai.route.resource_id") == arn
    assert span.get_tag("ai.route.resource_region") == "us-east-1"
    assert span.get_tag("ai.route.resource_owner_account_id") == "123456789012"
    assert span.get_tag("ai.gateway.deployment_id") == "dep-1"
    assert span.get_tag("ai.billing.account_id") is None
    assert span.get_tag("ai.billing.geography") is None
    assert "spoofed" not in repr(span)


def test_provider_model_id_overrides_bedrock_profile_and_is_not_a_gateway_deployment():
    arn = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/profile-1"
    previous = route_tags({"model": "bedrock/anthropic.claude", "model_id": arn})
    tags = route_tags({"model_id": "anthropic.claude-v2"}, previous)
    assert tags["ai.route.model_id"] == "anthropic.claude-v2"
    assert "ai.billing.resource_id" not in tags
    tags = route_tags({"model": "openai/gpt-4o", "model_id": arn})
    assert "ai.route.model_id" not in tags
    assert "ai.billing.resource_id" not in tags
    tags = route_tags({"api_base": "https://gateway.example"}, previous)
    assert tags["ai.route.resource_id"] == arn
    assert "ai.billing.resource_id" not in tags


@pytest.mark.parametrize("suffix", ["services.ai.azure.com", "models.ai.azure.com", "openai.azure.com"])
def test_azure_ai_official_and_lookalike_endpoints(suffix):
    for host, official in ((f"resource.{suffix}", True), (f"resource.{suffix}.evil.test", False)):
        tags = route_tags({"model": "azure_ai/claude-sonnet-4-6", "api_base": f"https://{host}/anthropic"})
        assert (tags.get("ai.billing.provider") == "azure") is official
        assert "ai.billing.account_id" not in tags
        assert "ai.billing.geography" not in tags
    assert route_tags({"model": "azure_ai/claude-sonnet-4-6"})["ai.billing.provider"] == "azure"


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
    assert tags["ai.billing.provider"] == "oracle"
    assert tags["ai.route.oci_tenancy"] == "ocid1.tenancy.oc1..test"
    assert "ai.billing.account_id" not in tags
    assert tags["ai.billing.project_id"] == "ocid1.compartment.oc1..test"
    assert tags["ai.route.oci_region"] == "us-ashburn-1"
    assert "PRIVATE" not in repr(tags)
    for host, official in (
        ("inference.generativeai.us-ashburn-1.oci.oraclecloud.com", True),
        ("inference.generativeai.us-ashburn-1.oci.oraclecloud.com.evil.test", False),
        ("gateway.example", False),
    ):
        resolved = route_tags({"api_base": "https://" + host}, tags)
        assert (resolved.get("ai.billing.provider") == "oracle") is official
        assert ("ai.billing.project_id" in resolved) is official


@pytest.mark.parametrize(
    "key", ["oci_key", "oci_key_file", "oci_user", "oci_fingerprint", "oci_tenancy", "oci_compartment_id"]
)
async def test_client_oci_overrides_disable_operator_scope(key):
    records = []
    callback = make_callback({"dep-1": SCOPE}, sink=records.append)
    data = await start(callback, data={key: "PRIVATE"})
    await finish(callback, data, response(Usage(prompt_tokens=3, completion_tokens=1)))
    assert "ai.billing.account_id" not in records[0].tags
    assert "client_credentials_or_endpoint" in records[0].tags["ai.attribution.issues"]
    assert "PRIVATE" not in repr(records)


@pytest.mark.parametrize(
    "traffic,mode",
    [
        ("ON_DEMAND", "standard"),
        ("ON_DEMAND_PRIORITY", "priority"),
        ("ON_DEMAND_FLEX", "flex"),
        ("PROVISIONED_THROUGHPUT", "provisioned_throughput"),
    ],
)
async def test_vertex_returned_traffic_overrides_config_and_reaches_apm(tracer, test_spans, traffic, mode):
    scope = BillingScope("gcp", "billing-1", "vertex-ai", mode="standard", geography="global")
    callback = make_callback({"dep-1": scope}, sink=DatadogSink(tracer))
    data = await start(callback, data={"metadata": {"traffic_type": "spoofed"}})
    await callback.async_pre_call_deployment_hook(
        {**data, "model": "vertex_ai/gemini-2.5-pro", "model_info": {"id": "dep-1"}}, "completion"
    )
    result = response(Usage(prompt_tokens=10, completion_tokens=1), service_tier="default")
    result._hidden_params["provider_specific_fields"] = {"traffic_type": traffic, "thought_signature": "PRIVATE"}
    await finish(callback, data, result)
    span = test_spans.pop()[0]
    assert span.get_tag("ai.observed.traffic_type") == traffic
    assert span.get_tag("ai.observed.service_tier") == "default"
    assert span.get_tag("ai.billing.mode") == mode
    assert span.get_tag("ai.billing.mode_source") == "response_traffic_type"
    assert ("conflicting_response_billing_mode" in span.get_tag("ai.attribution.issues")) is (
        mode in ("priority", "flex")
    )
    assert "PRIVATE" not in repr(span)
    assert "spoofed" not in repr(span)


@pytest.mark.parametrize("traffic", [None, "TRAFFIC_TYPE_UNSPECIFIED", "PRIVATE", {"secret": "PRIVATE"}])
def test_unknown_vertex_traffic_is_not_exported_or_used_as_billing_mode(traffic):
    result = {"_hidden_params": {"provider_specific_fields": {"traffic_type": traffic}}}
    assert not response_tags(result, "vertex_ai")
    result["_hidden_params"]["provider_specific_fields"]["traffic_type"] = "PROVISIONED_THROUGHPUT"
    assert not response_tags(result, "openai")


def test_response_request_ids_are_bounded_allowlisted_and_unambiguous():
    headers = {
        "llm_provider-X-Request-ID": "request-openai",
        "llm_provider-request-id": "request-anthropic",
        "llm_provider-x-amzn-requestid": "request-bedrock",
        "llm_provider-apim-request-id": "request-azure",
        "llm_provider-opc-request-id": "request-oci",
        "authorization": "Bearer PRIVATE",
        "llm_provider-set-cookie": "PRIVATE",
        "llm_provider-PRIVATE": "PRIVATE",
    }
    tags = response_tags({"_hidden_params": {"additional_headers": headers}}, "openai")
    assert len(tags) == 5
    assert tags["ai.response.x_request_id"] == "request-openai"
    assert tags["ai.response.x_amzn_requestid"] == "request-bedrock"
    assert "PRIVATE" not in repr(tags)
    headers["llm_provider-x-request-id"] = "different"
    headers["llm_provider-request-id"] = "sk-PRIVATE"
    headers["llm_provider-apim-request-id"] = "x" * 257
    headers["llm_provider-opc-request-id"] = {"PRIVATE": "PRIVATE"}
    tags = response_tags({"_hidden_params": {"additional_headers": headers}}, "openai")
    assert tags == {"ai.response.x_amzn_requestid": "request-bedrock"}
    assert not response_tags({"_hidden_params": {"additional_headers": dict.fromkeys(map(str, range(129)))}}, "openai")


def test_long_resource_ids_remain_exact_with_a_separate_bound():
    resource = (
        "/subscriptions/sub/resourceGroups/"
        + "g" * 100
        + "/providers/Microsoft.CognitiveServices/accounts/"
        + "a" * 100
    )
    scope = BillingScope("azure", "sub", "foundry", resource_id=resource)
    assert scope.tags()["ai.billing.resource_id"] == resource
    assert len(resource) > 256
    for invalid in ("x" * 2049, "sk-PRIVATE", "resource\nPRIVATE", {"PRIVATE": "PRIVATE"}):
        with pytest.raises(ValueError):
            BillingScope("azure", "sub", "foundry", resource_id=invalid)


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
