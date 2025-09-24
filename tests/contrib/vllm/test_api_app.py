from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from .api_app import app


@pytest.mark.snapshot(ignores=[
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
    "meta._dd.p.llmobs_trace_id"
])
def test_rag_parent_child(vllm, mock_tracer, llmobs_events, vllm_engine_mode):
    os.environ["VLLM_USE_V1"] = vllm_engine_mode

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

    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]

    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    print("---SPANS---")
    print(spans)
    print("---END SPANS---")


