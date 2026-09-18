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
from urllib.parse import unquote

import httpx
import litellm
import msgpack
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[4]
BEDROCK_PROFILE = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/local-profile"


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    temp = tmp_path_factory.mktemp("gateway")
    traces = []
    provider_requests = []

    class MockHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def provider_scope_headers(self):
            if self.path in ("/v1/messages", "/anthropic/v1/messages"):
                self.send_header("anthropic-organization-id", "org-anthropic-response")
                self.send_header("anthropic-workspace-id", "wrkspc-response")
            elif self.path in ("/v1/chat/completions", "/v1/responses", "/v1/embeddings"):
                self.send_header("openai-organization", "org-openai-response")
                self.send_header("openai-project", "proj-openai-response")

        def respond(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-request-id", "upstream-request-local")
            self.send_header("set-cookie", "PRIVATE COOKIE")
            self.provider_scope_headers()
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
            self.provider_scope_headers()
            self.end_headers()
            for event in events:
                self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()

        def do_POST(self):
            if self.path == "/v0.4/traces":
                return self.do_PUT()
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            provider_requests.append(
                {**data, "observed_project_header": self.headers.get("OpenAI-Project"), "observed_path": self.path}
            )
            if self.path.startswith("/model/") and self.path.endswith("/converse"):
                self.respond(
                    {
                        "output": {"message": {"role": "assistant", "content": [{"text": "PRIVATE OUTPUT"}]}},
                        "stopReason": "end_turn",
                        "usage": {
                            "inputTokens": 60,
                            "outputTokens": 25,
                            "totalTokens": 85,
                            "cacheReadInputTokens": 40,
                            "cacheWriteInputTokens": 0,
                        },
                        "metrics": {"latencyMs": 10},
                    }
                )
                return
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
            if self.path in ("/v1/messages", "/anthropic/v1/messages"):
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
                self.provider_scope_headers()
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
                        "service_tier": "Future_Response_Tier",
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
                    "extra_headers": {"OpenAI-Project": "proj-router"},
                    "timeout": 5,
                },
                "model_info": {"id": deployment, "datadog_provider_api_key_id": f"key_{deployment}"},
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
        "general_settings": {
            "custom_auth": "tests.contrib.litellm.gateway.proxy_auth.authenticate",
            "user_header_mappings": [{"header_name": "x-app-user", "litellm_user_role": "customer"}],
        },
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
            "model_info": {"id": "anthropic-deployment", "datadog_provider_api_key_id": "apikey_anthropic"},
        }
    )
    config_path = temp / "config.yaml"
    models.append(
        {
            "model_name": "azure-claude",
            "litellm_params": {
                "model": "azure_ai/claude-sonnet-4-6",
                "api_key": "SYNTHETIC-AZURE-SECRET",
                "api_base": f"{local}/anthropic",
                "timeout": 5,
            },
            "model_info": {"id": "azure-ai-deployment"},
        }
    )
    models.append(
        {
            "model_name": "bedrock-profile",
            "litellm_params": {
                "model": "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
                "model_id": BEDROCK_PROFILE,
                "aws_region_name": "us-east-1",
                "aws_access_key_id": "SYNTHETIC-AWS-ACCESS",
                "aws_secret_access_key": "SYNTHETIC-AWS-SECRET",
                "aws_bedrock_runtime_endpoint": local,
                "timeout": 5,
            },
            "model_info": {"id": "bedrock-deployment"},
        }
    )
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
            "user": "claimed-user",
            "model_info": {"datadog_provider_api_key_id": "spoofed-key"},
            "metadata": {
                "user_api_key_user_id": "SPOOFED USER",
                "usr.email": "spoofed@example.test",
                "_dd_gateway_attribution_token": "forged",
                "billing_account_id": "spoofed-org",
                "datadog_provider_api_key_id": "spoofed-key",
            },
        }
        if stream:
            data["stream_options"] = {"include_usage": True}
        async with httpx.AsyncClient(timeout=20) as client:
            return await client.post(
                f"{url}/v1/chat/completions",
                json=data,
                headers={"Authorization": f"Bearer test-{user}", "anthropic-workspace-id": "spoofed-workspace"},
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
    assert sorted(s["meta"]["ai.gateway.deployment_id"] for s in successful) == [
        "fallback-deployment",
        "openai-deployment",
        "openai-deployment",
    ]
    assert all(not any(key.startswith("ai.billing.") for key in span["meta"]) for span in spans)
    for span in successful:
        # The OpenAI-shaped mock has no cache-write counter. Preserve totals and
        # cache reads without silently fabricating a complete uncached partition.
        assert "ai.usage.input_uncached_tokens" not in span["metrics"]
        assert span["metrics"]["ai.observed.input_tokens"] == 100
        assert span["metrics"]["ai.observed.input_cache_write_reported"] == 0
        assert "cache_write_detail_missing" in span["meta"]["ai.attribution.issues"]
        assert span["metrics"]["ai.usage.input_cache_read_tokens"] == 40
        assert span["metrics"]["ai.usage.output_tokens"] == 25
        assert span["metrics"]["ai.observed.context_tokens"] == 100
        assert span["meta"]["ai.route.provider"] == "openai"
        assert span["meta"]["ai.route.endpoint_host"] == "127.0.0.1"
        assert span["meta"]["ai.route.api_key_id"] == f"key_{span['meta']['ai.gateway.deployment_id']}"
        assert span["meta"]["ai.response.openai_organization"] == "org-openai-response"
        assert span["meta"]["ai.response.openai_project"] == "proj-openai-response"
        assert "ai.response.anthropic_workspace_id" not in span["meta"]
        if span["meta"]["usr.id"] == "alice":
            assert span["meta"]["ai.response.x_request_id"] == "upstream-request-local"
            assert span["meta"]["ai.observed.service_tier"] == "Future_Response_Tier"
        assert span["meta"]["ai.route.organization"] == "org-router"
        assert span["meta"]["ai.route.project"] == "proj-router"
        assert span["meta"]["ai.request.service_tier"] == "priority"
        assert span["meta"]["ai.effective.service_tier"] == "priority"
    serialized = json.dumps(spans)
    for span in spans:
        assert span["meta"]["usr.id"] in ("alice", "bob")
        assert span["meta"]["ai.identity.source"] == "gateway_auth"
        assert span["meta"]["ai.end_user.id"] == "claimed-user"
        assert span["meta"]["ai.end_user.trust"] == "unverified"
    for secret in (
        "PRIVATE PROMPT",
        "PRIVATE OUTPUT",
        "PRIVATE COOKIE",
        "SYNTHETIC-PROVIDER-SECRET",
        "SPOOFED",
        "spoofed",
    ):
        assert secret not in serialized
    assert all(s["parent_id"] != 0 for s in spans)
    assert len(upstream) == 5  # 3 successes + initial fallback failure + final error
    assert all(request["observed_project_header"] == "proj-router" for request in upstream)


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
    assert {s["meta"]["ai.gateway.deployment_id"] for s in spans} == {
        "openai-deployment",
        "anthropic-deployment",
    }
    for s in spans:
        if s["meta"]["ai.operation"] == "anthropic_messages":
            assert s["metrics"]["ai.usage.input_uncached_tokens"] == 60, s
            assert s["meta"]["ai.route.api_key_id"] == "apikey_anthropic"
            assert s["meta"]["ai.response.anthropic_organization_id"] == "org-anthropic-response"
            assert s["meta"]["ai.response.anthropic_workspace_id"] == "wrkspc-response"
        else:
            assert s["meta"]["ai.route.api_key_id"] == "key_openai-deployment"
            assert s["meta"]["ai.response.openai_organization"] == "org-openai-response"
            assert s["meta"]["ai.response.openai_project"] == "proj-openai-response"
            assert "ai.usage.input_uncached_tokens" not in s["metrics"]
            assert s["metrics"]["ai.observed.input_tokens"] == 100
            assert "cache_write_detail_missing" in s["meta"]["ai.attribution.issues"]
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
    assert "ai.billing.provider" not in modal["meta"]
    assert "PRIVATE" not in json.dumps(spans)


async def test_bedrock_model_id_survives_real_router_and_provider_hooks(gateway):
    url, traces, upstream = gateway
    async with httpx.AsyncClient(timeout=20) as client:
        result = await client.post(
            f"{url}/chat/completions",
            headers={"Authorization": "Bearer test-alice"},
            json={"model": "bedrock-profile", "messages": [{"role": "user", "content": "PRIVATE PROMPT"}]},
        )
    assert result.status_code == 200, result.text
    deadline = time.monotonic() + 15
    spans = []
    while time.monotonic() < deadline:
        spans = [
            span
            for trace in list(traces)
            for span in trace
            if span.get("name") == "ai_gateway.usage"
            and span["meta"].get("ai.gateway.deployment_id") == "bedrock-deployment"
        ]
        if spans:
            break
        await asyncio.sleep(0.2)
    assert len(spans) == 1
    span = spans[0]
    assert span["meta"]["ai.route.model_id"] == BEDROCK_PROFILE
    assert span["meta"]["ai.route.aws_region_name"] == "us-east-1"
    assert "ai.billing.account_id" not in span["meta"]
    assert "ai.billing.provider" not in span["meta"]
    assert any(BEDROCK_PROFILE in unquote(request["observed_path"]) for request in upstream)
    for private in ("PRIVATE", "SYNTHETIC-AWS-ACCESS", "SYNTHETIC-AWS-SECRET"):
        assert private not in json.dumps(spans)


@pytest.mark.parametrize("stream", [False, True])
async def test_raw_azure_claude_route_reaches_wire(gateway, stream):
    url, traces, upstream = gateway
    before = {span["span_id"] for trace in list(traces) for span in trace}
    async with httpx.AsyncClient(timeout=20) as client:
        result = await client.post(
            f"{url}/chat/completions",
            headers={"Authorization": "Bearer test-alice"},
            json={
                "model": "azure-claude",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "PRIVATE PROMPT"}],
                "stream": stream,
            },
        )
    assert result.status_code == 200, result.text
    deadline = time.monotonic() + 15
    spans = []
    while time.monotonic() < deadline:
        spans = [
            span
            for trace in list(traces)
            for span in trace
            if span.get("name") == "ai_gateway.usage"
            and span["span_id"] not in before
            and span["meta"].get("ai.gateway.deployment_id") == "azure-ai-deployment"
        ]
        if spans:
            break
        await asyncio.sleep(0.2)
    assert len(spans) == 1
    span = spans[0]
    assert span["meta"]["usr.id"] == "alice"
    assert span["meta"]["ai.route.provider"] == "azure_ai"
    assert not any(key.startswith("ai.billing.") for key in span["meta"])
    assert span["metrics"]["ai.observed.context_tokens"] == 100
    assert span["metrics"]["ai.usage.input_cache_read_tokens"] == 40
    assert span["metrics"]["ai.usage.output_tokens"] == 25
    assert any(request["observed_path"] == "/anthropic/v1/messages" for request in upstream)
    assert "PRIVATE" not in json.dumps(spans)
    assert "SYNTHETIC-AZURE-SECRET" not in json.dumps(spans)


@pytest.mark.parametrize(
    "endpoint,fields,headers,expected",
    [
        ("chat/completions", {"user": "body-user"}, {}, "body-user"),
        ("chat/completions", {}, {"x-litellm-end-user-id": "header-user"}, "header-user"),
        (
            "chat/completions",
            {"user": "ignored-body-user"},
            {"x-litellm-customer-id": "customer-user", "x-litellm-end-user-id": "ignored-header-user"},
            "customer-user",
        ),
        ("chat/completions", {}, {"x-app-user": "mapped-user"}, "mapped-user"),
        ("messages", {"litellm_metadata": {"user": "metadata-user"}}, {}, "metadata-user"),
        ("messages", {"metadata": {"user_id": "anthropic-user"}}, {}, "anthropic-user"),
        ("responses", {"safety_identifier": "response-user"}, {}, "response-user"),
        ("chat/completions", {}, {}, None),
    ],
)
async def test_end_user_fallback_through_real_proxy(gateway, endpoint, fields, headers, expected):
    url, traces, _ = gateway
    before = {s["span_id"] for t in traces for s in t}
    data = {"model": "test-model", "messages": [{"role": "user", "content": "PRIVATE PROMPT"}]}
    if endpoint == "messages":
        data.update(model="test-claude", max_tokens=100)
    elif endpoint == "responses":
        data.pop("messages")
        data["input"] = "PRIVATE PROMPT"
    data.update(fields)
    async with httpx.AsyncClient(timeout=20) as client:
        result = await client.post(
            f"{url}/v1/{endpoint}",
            json=data,
            headers={"Authorization": "Bearer test-unassigned", "anthropic-version": "2023-06-01", **headers},
        )
    assert result.status_code == 200, result.text
    deadline = time.monotonic() + 15
    spans = []
    while time.monotonic() < deadline:
        spans = [
            s for t in list(traces) for s in t if s.get("name") == "ai_gateway.usage" and s["span_id"] not in before
        ]
        if spans:
            break
        await asyncio.sleep(0.2)
    assert len(spans) == 1, spans
    tags = spans[0]["meta"]
    assert tags.get("usr.id") == expected
    assert tags.get("ai.end_user.id") == expected
    assert tags["ai.identity.source"] == ("litellm_end_user" if expected else "unknown")
    assert tags.get("ai.end_user.trust") == ("unverified" if expected else None)
    assert tags["ai.attribution.status"] == "incomplete"
    assert "authenticated_user_unknown" in tags["ai.attribution.issues"]
    assert spans[0]["metrics"]["ai.usage.output_tokens"] == 25
    assert not any(value in json.dumps(spans) for value in ("PRIVATE", "SYNTHETIC", "ignored-", "test-unassigned"))
