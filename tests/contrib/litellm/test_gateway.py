import asyncio
from datetime import datetime
from datetime import timezone
import time
from types import SimpleNamespace

from litellm import ModelResponse
from litellm import Usage
import pytest

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
    await finish(callback, data, response(Usage(prompt_tokens=20, completion_tokens=5)))
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
            normalize_usage({"prompt_tokens": 4, "completion_tokens": 2}),
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
    assert result.quantities["input_uncached_tokens"] == 60
    assert result.quantities["input_cache_read_tokens"] == 40
    assert result.quantities["output_tokens"] == 25
    assert result.diagnostics["context_tokens"] == 100
    assert not result.issues


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
        "Invalid gateway attribution configuration; billing and optional identity enrichment disabled"
    )
    assert not callback._routes
    assert not callback._capture_email
    assert not callback._auth_metadata_keys


def test_missing_configuration_keeps_identity_only(monkeypatch):
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
