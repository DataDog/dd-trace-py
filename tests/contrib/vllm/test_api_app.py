from __future__ import annotations

from unittest import mock

from fastapi.testclient import TestClient
import pytest

from ddtrace import tracer as ddtracer
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_metrics
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.propagation.http import HTTPPropagator
from tests.llmobs._utils import assert_llmobs_span_data

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
def test_rag_parent_child(vllm, vllm_llmobs, test_spans):
    """Test RAG endpoint with parent-child span relationships and LLMObs event capture."""
    # Create a parent span and inject context into headers. ``ddtracer`` is the same
    # global tracer wrapped by ``test_spans``; spans land in the same container.
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

    # Should have spans for: embed doc1, embed doc2, embed query, generate text (4 LLMObs spans),
    # plus the parent ``api.rag`` span which is not an LLMObs span.
    spans = [s for trace in test_spans.pop_traces() for s in trace]

    llmobs_spans = [s for s in spans if _get_llmobs_data_metastruct(s)]
    assert len(llmobs_spans) == 4

    embedding_spans = [s for s in llmobs_spans if get_llmobs_span_kind(s) == "embedding"]
    generation_spans = [s for s in llmobs_spans if get_llmobs_span_kind(s) == "llm"]

    assert len(embedding_spans) == 3  # 2 docs + 1 query
    assert len(generation_spans) == 1

    expected_embedding_docs = {
        "Paris is the capital and most populous city of France.",
        "Berlin is Germany's capital.",
        "What is the capital of France?",
    }
    captured_docs = set()
    for span in embedding_spans:
        meta_struct = _get_llmobs_data_metastruct(span)
        documents = meta_struct.get("meta", {}).get("input", {}).get("documents", [])
        assert len(documents) == 1
        captured_docs.add(documents[0]["text"])
    assert captured_docs == expected_embedding_docs

    # Verify all embedding spans have correct structure. Distributed-context propagation
    # via HTTPPropagator places the request-side spans on the same APM trace as ``api.rag``.
    for span in embedding_spans:
        assert span.trace_id == parent_span.trace_id
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="embedding",
            model_name="intfloat/e5-small-v2",
            model_provider="vllm",
            metadata={"embedding_dim": 384, "num_cached_tokens": 0},
            metrics={
                "output_tokens": 0,
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
        )
        # input_tokens > 0 sanity check
        metrics = get_llmobs_metrics(span) or {}
        assert metrics.get("input_tokens", 0) > 0

    # Verify generation span has correct structure
    gen_span = generation_spans[0]
    assert gen_span.trace_id == parent_span.trace_id
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(gen_span),
        span_kind="llm",
        model_name="facebook/opt-125m",
        model_provider="vllm",
        metadata={
            "temperature": 0.8,
            "top_p": 0.95,
            "max_tokens": 64,
            "n": 1,
            "num_cached_tokens": 0,
        },
        metrics={
            "input_tokens": 27,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_decode": mock.ANY,
            "time_in_model_inference": mock.ANY,
        },
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
    )
    gen_metrics = get_llmobs_metrics(gen_span) or {}
    assert gen_metrics.get("output_tokens", 0) > 0
