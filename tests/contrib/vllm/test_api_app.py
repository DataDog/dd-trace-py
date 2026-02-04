from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from ddtrace import tracer as ddtracer
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.propagation.http import HTTPPropagator
from tests.utils import override_global_config

from .api_app import app


IGNORE_FIELDS = [
    "meta._dd.p.llmobs_trace_id",
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
    "metrics.vllm.latency.prefill",
    "metrics.vllm.latency.decode",
    "metrics.vllm.latency.inference",
]


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_rag_parent_child(vllm, llmobs_span_writer):
    """Test RAG endpoint with parent-child span relationships and LLMObs event capture."""
    # Enable LLMObs on ddtracer with integrations enabled and use test writer
    llmobs_service.disable()
    with override_global_config({"_llmobs_ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"}):
        llmobs_service.enable(_tracer=ddtracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer

        # Create a parent span and inject context into headers
        with ddtracer.trace("api.rag") as parent_span:
            headers = {}
            HTTPPropagator.inject(parent_span.context, headers)

            client = TestClient(app)
            payload = {
                "query": "What is the capital of France?",
                "documents": [
                    "Paris is the capital and most populous city of France.",
                    "Berlin is Germany's capital.",
                ],
            }

            res = client.post("/rag", json=payload, headers=headers)
            assert res.status_code == 200

        llmobs_service.disable()

    # Verify LLMObs events were captured
    # Should have events for: embed doc1, embed doc2, embed query, generate text
    llmobs_events = llmobs_span_writer.events
    assert len(llmobs_events) == 4

    # Verify we have both embedding and completion operations
    span_kinds = [event["meta"]["span"]["kind"] for event in llmobs_events]
    assert span_kinds.count("embedding") == 3  # 2 docs + 1 query
    assert span_kinds.count("llm") == 1  # 1 generation

    # Check embedding events (order may vary)
    embedding_docs = {
        "Paris is the capital and most populous city of France.",
        "Berlin is Germany's capital.",
        "What is the capital of France?",
    }

    embedding_events = [e for e in llmobs_events if e["meta"]["span"]["kind"] == "embedding"]
    generation_events = [e for e in llmobs_events if e["meta"]["span"]["kind"] == "llm"]

    captured_docs = {e["meta"]["input"]["documents"][0]["text"] for e in embedding_events}
    assert captured_docs == embedding_docs

    # Verify all embedding events have correct structure
    for event in embedding_events:
        assert event["meta"]["model_name"] == "intfloat/e5-small-v2"
        assert event["meta"]["model_provider"] == "vllm"
        assert event["meta"]["metadata"]["embedding_dim"] == 384
        assert event["meta"]["metadata"]["num_cached_tokens"] == 0
        assert event["metrics"]["input_tokens"] > 0
        assert event["metrics"]["output_tokens"] == 0
        assert "time_to_first_token" in event["metrics"]
        assert "time_in_queue" in event["metrics"]
        assert "time_in_model_prefill" in event["metrics"]
        assert "time_in_model_inference" in event["metrics"]
        assert "ml_app:<ml-app-name>" in event["tags"]
        assert "service:tests.contrib.vllm" in event["tags"]

    # Verify generation event has correct structure
    assert len(generation_events) == 1
    gen_event = generation_events[0]
    assert gen_event["meta"]["model_name"] == "facebook/opt-125m"
    assert gen_event["meta"]["model_provider"] == "vllm"
    assert gen_event["meta"]["metadata"]["temperature"] == 0.8
    assert gen_event["meta"]["metadata"]["top_p"] == 0.95
    assert gen_event["meta"]["metadata"]["max_tokens"] == 64
    assert gen_event["meta"]["metadata"]["n"] == 1
    assert gen_event["meta"]["metadata"]["num_cached_tokens"] == 0
    assert gen_event["metrics"]["input_tokens"] == 27
    assert gen_event["metrics"]["output_tokens"] > 0
    assert "time_to_first_token" in gen_event["metrics"]
    assert "time_in_queue" in gen_event["metrics"]
    assert "time_in_model_prefill" in gen_event["metrics"]
    assert "time_in_model_decode" in gen_event["metrics"]
    assert "time_in_model_inference" in gen_event["metrics"]
    assert "ml_app:<ml-app-name>" in gen_event["tags"]
    assert "service:tests.contrib.vllm" in gen_event["tags"]
