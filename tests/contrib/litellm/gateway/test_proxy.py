"""Real LiteLLM HTTP proxy + real ddtrace encoding; only provider and Agent are fake."""

import asyncio
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import litellm
import msgpack
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    temp = tmp_path_factory.mktemp("gateway")
    traces = []
    provider_requests = []

    class MockHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.respond({"endpoints": ["/v0.4/traces"]})

        def do_PUT(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/v0.4/traces":
                traces.extend(msgpack.unpackb(body, raw=False, strict_map_key=False))
            self.respond({"rate_by_service": {}})

        def events(self, events):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in events:
                self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()

        def do_POST(self):
            if self.path == "/v0.4/traces":
                return self.do_PUT()
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            provider_requests.append(data)
            if self.path == "/v1/embeddings":
                self.respond(
                    {
                        "object": "list",
                        "model": "text-embedding-3-small",
                        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                        "usage": {"prompt_tokens": 12, "total_tokens": 12},
                    }
                )
                return
            if self.path == "/v1/messages":
                message = {
                    "id": "msg_local",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-20250514",
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "content": [{"type": "text", "text": "PRIVATE OUTPUT"}],
                    "usage": {
                        "input_tokens": 60,
                        "output_tokens": 25,
                        "cache_read_input_tokens": 40,
                        "cache_creation_input_tokens": 0,
                    },
                }
                if data.get("stream"):
                    self.events(
                        [
                            {
                                "type": "message_start",
                                "message": {
                                    **message,
                                    "content": [],
                                    "stop_reason": None,
                                    "usage": {**message["usage"], "output_tokens": 0},
                                },
                            },
                            {
                                "type": "content_block_start",
                                "index": 0,
                                "content_block": {"type": "text", "text": ""},
                            },
                            {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": "PRIVATE OUTPUT"},
                            },
                            {"type": "content_block_stop", "index": 0},
                            {
                                "type": "message_delta",
                                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                "usage": {"output_tokens": 25},
                            },
                            {"type": "message_stop"},
                        ]
                    )
                else:
                    self.respond(message)
                return
            if self.path == "/v1/responses":
                response = {
                    "id": "resp_local",
                    "object": "response",
                    "created_at": 1720000000,
                    "model": "gpt-4o-2024-08-06",
                    "status": "completed",
                    "error": None,
                    "incomplete_details": None,
                    "instructions": None,
                    "metadata": {},
                    "output": [
                        {
                            "type": "message",
                            "id": "msg_local",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "PRIVATE OUTPUT", "annotations": []}],
                        }
                    ],
                    "parallel_tool_calls": True,
                    "tool_choice": "auto",
                    "tools": [],
                    "temperature": 1,
                    "top_p": 1,
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 25,
                        "total_tokens": 125,
                        "input_tokens_details": {"cached_tokens": 40},
                        "output_tokens_details": {"reasoning_tokens": 5},
                    },
                }
                if data.get("stream"):
                    self.events(
                        [
                            {
                                "type": "response.created",
                                "sequence_number": 0,
                                "response": {
                                    **response,
                                    "status": "in_progress",
                                    "output": [],
                                    "usage": None,
                                },
                            },
                            {
                                "type": "response.output_text.delta",
                                "sequence_number": 1,
                                "item_id": "msg_local",
                                "output_index": 0,
                                "content_index": 0,
                                "delta": "PRIVATE OUTPUT",
                                "logprobs": [],
                            },
                            {
                                "type": "response.completed",
                                "sequence_number": 2,
                                "response": response,
                            },
                        ]
                    )
                else:
                    self.respond(response)
                return
            if data["model"] == "gpt-4o-failing":
                self.respond(
                    {"error": {"message": "synthetic upstream failure", "type": "server_error"}},
                    503,
                )
                return
            usage = {
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "total_tokens": 125,
                "prompt_tokens_details": {"cached_tokens": 40},
                "completion_tokens_details": {"reasoning_tokens": 5},
            }
            if data["model"] == "gpt-4o-modal":
                usage["prompt_tokens_details"] = {"text_tokens": 70, "image_tokens": 30, "cached_tokens": 5}
            base = {
                "id": "chatcmpl-local-test",
                "model": "gpt-4o-2024-08-06",
                "created": 1720000000,
            }
            if data.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for item in [
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": "PRIVATE OUTPUT"},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                    {"choices": [], "usage": usage},
                ]:
                    chunk = {**base, "object": "chat.completion.chunk", **item}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                self.respond(
                    {
                        **base,
                        "object": "chat.completion",
                        "usage": usage,
                        "service_tier": "default",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "PRIVATE OUTPUT"},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = f"http://127.0.0.1:{server.server_port}"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    models = []
    for alias, model, deployment in [
        ("test-model", "gpt-4o", "openai-deployment"),
        ("fail-model", "gpt-4o-failing", "failed-deployment"),
        ("fallback-model", "gpt-4o", "fallback-deployment"),
        ("error-model", "gpt-4o-failing", "error-deployment"),
        ("embedding-model", "text-embedding-3-small", "embedding-deployment"),
        ("multimodal-model", "gpt-4o-modal", "multimodal-deployment"),
    ]:
        models.append(
            {
                "model_name": alias,
                "litellm_params": {
                    "model": f"openai/{model}",
                    "api_key": "sk-SYNTHETIC-PROVIDER-SECRET",
                    "api_base": f"{local}/v1",
                    "organization": "org-router",
                    "timeout": 5,
                },
                "model_info": {"id": deployment},
            }
        )
    config = {
        "model_list": models,
        "litellm_settings": {
            "callbacks": ["ddtrace.contrib.litellm.gateway_attribution"],
            "turn_off_message_logging": True,
            "telemetry": False,
            "request_timeout": 10,
            "num_retries": 0,
        },
        "general_settings": {"custom_auth": "tests.contrib.litellm.gateway.proxy_auth.authenticate"},
        "router_settings": {
            "num_retries": 0,
            "fallbacks": [{"fail-model": ["fallback-model"]}],
            "allowed_fails": 100,
        },
    }
    models.append(
        {
            "model_name": "test-claude",
            "litellm_params": {
                "model": "anthropic/claude-sonnet-4-20250514",
                "api_key": "sk-SYNTHETIC-PROVIDER-SECRET",
                "api_base": local,
                "timeout": 5,
            },
            "model_info": {"id": "anthropic-deployment"},
        }
    )
    config_path = temp / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    # Inherit only OS/runtime necessities, never real cloud/API/Datadog credentials.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT") if key in os.environ}
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "DD_TRACE_AGENT_URL": local,
            "DD_TRACE_API_VERSION": "v0.4",
            "DD_TRACE_ENABLED": "true",
            "DD_SERVICE": "gateway-attribution-test",
            "DD_ENV": "local-test",
            "DD_VERSION": "test",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
            "DD_REMOTE_CONFIGURATION_ENABLED": "false",
            "DD_TRACE_STARTUP_LOGS": "false",
            "DD_LLMOBS_ENABLED": "false",
            "DD_TRACE_LITELLM_ENABLED": "false",
            "DD_TRACE_OPENAI_ENABLED": "false",
            "DD_TRACE_ANTHROPIC_ENABLED": "false",
            "DD_TRACE_WRITER_INTERVAL_SECONDS": "0.1",
            "DD_TRACE_SAMPLE_RATE": "1",
            "TIKTOKEN_CACHE_DIR": str(Path(litellm.__file__).parent / "litellm_core_utils/tokenizers"),
        }
    )
    attribution_config = temp / "attribution.json"
    attribution_config.write_text(
        json.dumps(
            {
                "billing_scopes": {
                    "openai-deployment": {
                        "provider": "openai",
                        "account_id": "org-test",
                        "product": "api",
                        "project_id": "proj-test",
                        "geography": "global",
                        "mode": "standard",
                    },
                    "fallback-deployment": {
                        "provider": "azure",
                        "account_id": "sub-test",
                        "product": "foundry",
                        "resource_id": "resource-test",
                        "geography": "eastus",
                        "mode": "standard",
                    },
                    "anthropic-deployment": {
                        "provider": "anthropic",
                        "account_id": "anthropic-org-test",
                        "product": "platform-api",
                        "project_id": "workspace-test",
                        "geography": "global",
                        "mode": "standard",
                    },
                },
                "capture_email": True,
                "auth_metadata_keys": ["cost_center"],
            }
        )
    )
    env["DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG"] = str(attribution_config)
    output = (temp / "proxy.log").open("w+")
    command = [
        str(Path(sys.executable).parent / "ddtrace-run"),
        str(Path(sys.executable).parent / "litellm"),
        "--config",
        str(config_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    proc = None
    url = f"http://127.0.0.1:{port}"
    try:
        proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                output.seek(0)
                pytest.fail("LiteLLM startup failed:\n" + output.read()[-12000:])
            try:
                if httpx.get(f"{url}/health/liveliness", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.25)
        else:
            output.seek(0)
            pytest.fail("LiteLLM startup timeout:\n" + output.read()[-12000:])
        yield url, traces, provider_requests
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        output.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def test_real_proxy_and_wire_traces(gateway):
    url, traces, upstream = gateway

    async def request(user, model="test-model", stream=False):
        data = {
            "model": model,
            "messages": [{"role": "user", "content": "PRIVATE PROMPT"}],
            "stream": stream,
            "service_tier": "priority",
            "user": "SPOOFED USER",
            "metadata": {
                "user_api_key_user_id": "SPOOFED USER",
                "usr.email": "spoofed@example.test",
                "_dd_gateway_attribution_token": "forged",
                "billing_account_id": "spoofed-org",
            },
        }
        if stream:
            data["stream_options"] = {"include_usage": True}
        async with httpx.AsyncClient(timeout=20) as client:
            return await client.post(
                f"{url}/v1/chat/completions",
                json=data,
                headers={"Authorization": f"Bearer test-{user}"},
            )

    results = await asyncio.gather(
        request("alice"),
        request("bob", stream=True),
        request("alice", "fail-model"),
        request("bob", "error-model"),
    )
    assert [r.status_code for r in results] == [200, 200, 200, 503], [r.text for r in results]
    denied = await request("invalid")
    assert denied.status_code == 401
    deadline = time.monotonic() + 15
    spans = []
    while time.monotonic() < deadline:
        spans = [s for trace in list(traces) for s in trace if s.get("name") == "ai_gateway.usage"]
        if len(spans) >= 4:
            break
        await asyncio.sleep(0.2)
    assert len(spans) == 4, [(s.get("name"), s.get("meta")) for t in traces for s in t]
    tags = [s["meta"] for s in spans]
    assert sorted(t["usr.id"] for t in tags) == ["alice", "alice", "bob", "bob"]
    assert all(t["usr.email"] == f"{t['usr.id']}@example.test" for t in tags)
    assert all(t["ai.enrichment.cost_center"] == "test-eng" for t in tags)
    successful = [s for s in spans if s["meta"]["ai.request.outcome"] == "success"]
    assert len(successful) == 3
    assert sorted(s["meta"].get("ai.billing.account_id", "MISSING") for s in successful) == [
        "org-test",
        "org-test",
        "sub-test",
    ]
    for span in successful:
        assert span["metrics"]["ai.usage.input_uncached_tokens"] == 60
        assert span["metrics"]["ai.usage.input_cache_read_tokens"] == 40
        assert span["metrics"]["ai.usage.output_tokens"] == 25
        assert span["metrics"]["ai.observed.context_tokens"] == 100
        assert span["meta"]["ai.route.provider"] == "openai"
        assert span["meta"]["ai.route.endpoint_host"] == "127.0.0.1"
        assert span["meta"]["ai.route.organization"] == "org-router"
        assert span["meta"]["ai.request.service_tier"] == "priority"
        assert span["meta"]["ai.effective.service_tier"] == "priority"
    serialized = json.dumps(spans)
    for secret in (
        "PRIVATE PROMPT",
        "PRIVATE OUTPUT",
        "SYNTHETIC-PROVIDER-SECRET",
        "SPOOFED",
        "spoofed",
    ):
        assert secret not in serialized
    assert all(s["parent_id"] != 0 for s in spans)
    assert len(upstream) == 5  # 3 successes + initial fallback failure + final error


@pytest.mark.parametrize("stream", [False, True])
async def test_native_coding_agent_endpoints(gateway, stream):
    url, traces, _ = gateway
    before = {s["span_id"] for t in traces for s in t}
    async with httpx.AsyncClient(timeout=20) as client:
        claude = await client.post(
            f"{url}/v1/messages",
            headers={"x-api-key": "test-alice", "anthropic-version": "2023-06-01"},
            json={
                "model": "test-claude",
                "max_tokens": 100,
                "stream": stream,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "PRIVATE PROMPT",
                                "cache_control": {"type": "ephemeral", "ttl": "1h"},
                            }
                        ],
                    }
                ],
            },
        )
        assert claude.status_code == 200, claude.text
        codex = await client.post(
            f"{url}/v1/responses",
            headers={"Authorization": "Bearer test-bob"},
            json={"model": "test-model", "input": "PRIVATE PROMPT", "stream": stream},
        )
        assert codex.status_code == 200, codex.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        spans = [
            s
            for t in list(traces)
            for s in t
            if s.get("name") == "ai_gateway.usage"
            and s["span_id"] not in before
            and s["meta"].get("ai.operation") in ("anthropic_messages", "aresponses")
        ]
        if len(spans) >= 2:
            break
        await asyncio.sleep(0.2)
    assert len(spans) == 2, spans
    assert {s["meta"].get("ai.billing.account_id") for s in spans} == {
        "org-test",
        "anthropic-org-test",
    }
    for s in spans:
        assert s["metrics"]["ai.usage.input_uncached_tokens"] == 60, s
        assert s["metrics"]["ai.usage.input_cache_read_tokens"] == 40
        assert s["metrics"]["ai.usage.output_tokens"] == 25
        assert s["meta"]["usr.id"] == ("alice" if s["meta"]["ai.operation"] == "anthropic_messages" else "bob")
        if s["meta"]["ai.operation"] == "anthropic_messages":
            assert s["meta"]["ai.request.prompt_cache_ttls"] == "1h"
            assert s["meta"]["ai.effective.prompt_cache_ttls"] == "1h"
            assert s["meta"]["ai.effective.max_tokens"] == "100"


async def test_embeddings_and_multimodal_wire_counters(gateway):
    url, traces, _ = gateway
    before = {s["span_id"] for t in traces for s in t}
    async with httpx.AsyncClient(timeout=20) as client:
        embedding = await client.post(
            f"{url}/v1/embeddings",
            headers={"Authorization": "Bearer test-alice"},
            json={"model": "embedding-model", "input": "PRIVATE EMBEDDING INPUT", "dimensions": 2},
        )
        assert embedding.status_code == 200, embedding.text
        modal = await client.post(
            f"{url}/v1/chat/completions",
            headers={"Authorization": "Bearer test-bob"},
            json={
                "model": "multimodal-model",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": "https://example.test/PRIVATE_IMAGE"}}],
                    }
                ],
            },
        )
        assert modal.status_code == 200, modal.text
    deadline = time.monotonic() + 15
    spans = []
    while time.monotonic() < deadline:
        spans = [
            s for t in list(traces) for s in t if s.get("name") == "ai_gateway.usage" and s["span_id"] not in before
        ]
        if len(spans) >= 2:
            break
        await asyncio.sleep(0.2)
    assert len(spans) == 2, spans
    embedding = next(s for s in spans if s["meta"]["usr.id"] == "alice")
    assert embedding["meta"]["ai.operation"] in ("embedding", "aembedding")
    assert embedding["meta"]["ai.effective.dimensions"] == "2"
    assert embedding["metrics"]["ai.usage.input_uncached_tokens"] == 12
    modal = next(s for s in spans if s["meta"]["usr.id"] == "bob")
    assert modal["metrics"]["ai.observed.input_image_tokens"] == 30
    assert modal["metrics"]["ai.observed.input_text_tokens"] == 70
    assert modal["metrics"]["ai.observed.input_cache_read_tokens"] == 5
    assert "ai.usage.input_uncached_tokens" not in modal["metrics"]
    assert "ai.billing.provider" not in modal["meta"]  # Custom endpoint with no mapping.
    assert "PRIVATE" not in json.dumps(spans)
