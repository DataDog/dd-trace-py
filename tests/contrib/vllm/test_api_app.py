from __future__ import annotations

import os

from fastapi.testclient import TestClient
import pytest

from ddtrace import tracer as ddtracer
from ddtrace._trace.pin import Pin

from .api_app import app


@pytest.mark.snapshot(ignores=["metrics.vllm.latency.ttft", "metrics.vllm.latency.queue", "meta._dd.p.llmobs_trace_id"])
def test_rag_parent_child(vllm, mock_tracer, llmobs_events, vllm_engine_mode):
    os.environ["VLLM_USE_V1"] = vllm_engine_mode

    # Ensure snapshot writer receives traces: use global tracer for vLLM Pin
    pin = Pin.get_from(vllm)
    if pin is not None:
        pin._override(vllm, tracer=ddtracer)

    client = TestClient(app)
    payload = {
        "query": "What is the capital of France?",
        "documents": [
            "Paris is the capital and most populous city of France.",
            "Berlin is Germany's capital.",
        ],
    }

    res = client.post("/rag", json=payload)
    assert res.status_code == 200


@pytest.mark.snapshot(
    ignores=[
        "metrics.vllm.latency.ttft",
        "metrics.vllm.latency.queue",
    ]
)
def test_rag_mq_concurrency(vllm, mock_tracer, monkeypatch):
    monkeypatch.setenv("VLLM_USE_V1", "0")
    monkeypatch.setenv("VLLM_USE_MQ", "1")
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)

    client = TestClient(app)
    payload = {
        "query": "What is the capital of France?",
        "documents": [
            "Paris is the capital and most populous city of France.",
            "Berlin is Germany's capital.",
        ],
    }

    # Issue two requests to exercise MQ path
    r1 = client.post("/rag", json=payload)
    r2 = client.post("/rag", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200

    print("---RESULTS---")
    print(r1.json())
    print(r2.json())
    print("---END RESULTS---")

    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]
    print("---SPANS---")
    print(spans)
    print("---END SPANS---")
