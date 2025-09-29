from __future__ import annotations

import os

from fastapi.testclient import TestClient
import pytest

from ddtrace import tracer as ddtracer
from ddtrace._trace.pin import Pin

from .api_app import app


IGNORE_FIELDS = [
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
    "meta._dd.p.llmobs_trace_id",
]


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
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
