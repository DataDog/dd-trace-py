"""Provider-shaped payloads through LiteLLM's real response transformation, without network calls."""

from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from litellm import ModelResponse
from litellm.litellm_core_utils.litellm_logging import Logging
from litellm.litellm_core_utils.litellm_logging import get_standard_logging_object_payload
from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import VertexGeminiConfig
import pytest

from ddtrace.contrib.internal.litellm._gateway_usage import DatadogSink
from ddtrace.contrib.internal.litellm.gateway import GatewayAttribution
from ddtrace.vendor.dogstatsd import DogStatsd
from tests.contrib.litellm.gateway.test_proxy import Metrics


@pytest.fixture
def metrics():
    collector = Metrics()
    try:
        yield collector
    finally:
        collector.close()


@pytest.mark.parametrize(
    "traffic",
    [
        "ON_DEMAND",
        "ON_DEMAND_PRIORITY",
        "ON_DEMAND_FLEX",
        "PROVISIONED_THROUGHPUT",
        "TRAFFIC_TYPE_UNSPECIFIED",
        "FUTURE_TRAFFIC_TYPE",
    ],
)
async def test_vertex_native_traffic_metadata_survives_litellm_and_metrics(metrics, traffic):
    raw = httpx.Response(
        200,
        request=httpx.Request("POST", "https://us-central1-aiplatform.googleapis.com"),
        json={
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": "PRIVATE OUTPUT"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 10,
                "cachedContentTokenCount": 40,
                "totalTokenCount": 110,
                "trafficType": traffic,
            },
        },
    )
    result = VertexGeminiConfig().transform_response(
        model="gemini-2.5-pro",
        raw_response=raw,
        model_response=ModelResponse(),
        logging_obj=Mock(optional_params={}),
        request_data={},
        messages=[],
        optional_params={},
        litellm_params={},
        encoding=None,
    )
    callback = GatewayAttribution()
    callback._sink = DatadogSink(DogStatsd(host="127.0.0.1", port=metrics.socket.getsockname()[1]))
    data = {}
    await callback.async_pre_call_hook(SimpleNamespace(user_id="user-1"), None, data, "completion")
    await callback.async_pre_call_deployment_hook(
        {**data, "model": "vertex_ai/gemini-2.5-pro", "vertex_project": "project-1", "model_info": {"id": "dep-1"}},
        "completion",
    )
    await callback.async_log_success_event({"litellm_params": data}, result, None, None)
    group = (await metrics.wait(1))[0]
    assert group["tags"].get("ai.observed.traffic_type") == traffic
    assert group["tags"].get("ai.billing.mode") is None
    assert group["tags"].get("ai.route.vertex_project") == "project-1"
    assert group["counters"].get("ai_gateway.observed.input_tokens") == 100
    assert group["counters"].get("ai_gateway.usage.input_cache_read_tokens") == 40
    assert "PRIVATE" not in repr(group)


async def test_real_standard_logging_payload_supplies_common_fields(metrics):
    now = datetime.now(timezone.utc)
    messages = [{"role": "user", "content": "PRIVATE PROMPT"}]
    logging_obj = Logging("gpt-4o", messages, False, "completion", now, "local-call", "local-function")
    result = {"id": "local-response", "usage": {"prompt_tokens": 12, "completion_tokens": 3}}
    payload = get_standard_logging_object_payload(
        kwargs={
            "model": "gpt-4o",
            "custom_llm_provider": "openai",
            "call_type": "completion",
            "messages": messages,
            "litellm_params": {
                "api_base": "https://PRIVATE:PRIVATE@provider.example/PRIVATE",
                "metadata": {"model_info": {"id": "dep-1"}},
            },
        },
        init_response_obj=result,
        start_time=now,
        end_time=now,
        logging_obj=logging_obj,
        status="success",
    )
    assert payload is not None
    callback = GatewayAttribution()
    callback._sink = DatadogSink(DogStatsd(host="127.0.0.1", port=metrics.socket.getsockname()[1]))
    data = {}
    await callback.async_pre_call_hook(SimpleNamespace(user_id="user-1"), None, data, "completion")
    await callback.async_log_success_event(
        {"litellm_params": data, "standard_logging_object": payload}, result, now, now
    )
    group = (await metrics.wait(1))[0]
    assert group["tags"].get("ai.route.model") == payload["model"]
    assert group["tags"].get("ai.route.provider") == "openai"
    assert group["tags"].get("ai.route.endpoint_host") == "provider.example"
    assert group["tags"].get("ai.gateway.deployment_id") == "dep-1"
    assert group["tags"].get("ai.route.model_id") is None
    assert group["counters"].get("ai_gateway.observed.input_tokens") == 12
    assert "PRIVATE" not in repr(group)
