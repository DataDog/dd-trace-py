"""Provider-shaped payloads through LiteLLM's real response transformation, without network calls."""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from litellm import ModelResponse
from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import VertexGeminiConfig
import pytest

from ddtrace.contrib.internal.litellm._gateway_usage import DatadogSink
from ddtrace.contrib.internal.litellm.gateway import GatewayAttribution


@pytest.mark.parametrize("traffic", ["ON_DEMAND", "ON_DEMAND_PRIORITY", "ON_DEMAND_FLEX", "PROVISIONED_THROUGHPUT"])
async def test_vertex_native_traffic_metadata_survives_litellm_and_apm(tracer, test_spans, traffic):
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
    callback._sink = DatadogSink(tracer)
    data = {}
    await callback.async_pre_call_hook(SimpleNamespace(user_id="user-1"), None, data, "completion")
    await callback.async_pre_call_deployment_hook(
        {**data, "model": "vertex_ai/gemini-2.5-pro", "vertex_project": "project-1", "model_info": {"id": "dep-1"}},
        "completion",
    )
    await callback.async_log_success_event({"litellm_params": data}, result, None, None)
    span = test_spans.pop()[0]
    assert span.get_tag("ai.observed.traffic_type") == traffic
    assert span.get_tag("ai.billing.mode_source") == "response_traffic_type"
    assert span.get_tag("ai.billing.project_id") == "project-1"
    assert span.get_metric("ai.observed.input_tokens") == 100
    assert span.get_metric("ai.usage.input_cache_read_tokens") == 40
    assert "PRIVATE" not in repr(span)
