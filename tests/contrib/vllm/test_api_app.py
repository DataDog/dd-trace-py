from __future__ import annotations

import os

from fastapi.testclient import TestClient
import pytest

from ddtrace import tracer as ddtracer
from ddtrace._trace.pin import Pin
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.propagation.http import HTTPPropagator
from tests.utils import override_global_config

from .api_app import app


IGNORE_FIELDS = [
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
    "meta._dd.p.llmobs_trace_id",
]


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_rag_parent_child(vllm, mock_tracer, llmobs_span_writer, vllm_engine_mode):
    os.environ["VLLM_USE_V1"] = vllm_engine_mode

    # Ensure snapshot writer receives traces: use global tracer for vLLM Pin
    # Enable LLMObs on ddtracer with integrations enabled so vLLM spans get tagged
    pin = Pin.get_from(vllm)
    if pin is not None:
        pin._override(vllm, tracer=ddtracer)

    # Enable LLMObs on ddtracer with integrations enabled
    llmobs_service.disable()
    with override_global_config({"_llmobs_ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"}):
        llmobs_service.enable(_tracer=ddtracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer

        # Create a span and add inject context into headers
        with ddtracer.trace("api.rag") as span:
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            print(headers)
            try:
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
            finally:
                llmobs_service.disable()
