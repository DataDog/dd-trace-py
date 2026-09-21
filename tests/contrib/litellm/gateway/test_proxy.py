"""Real LiteLLM HTTP proxy and DogStatsD datagrams; provider and Agent intake are local fakes."""

import asyncio
from collections import defaultdict
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


class Metrics:
    def __init__(self):
        self.packets = []
        self.traces = []
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.1)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.receive, daemon=True)
        self.thread.start()

    def receive(self):
        while not self.stopped.is_set():
            try:
                data = self.socket.recv(65535)
            except socket.timeout:
                continue
            for line in data.decode().splitlines():
                name_value, kind, *fields = line.split("|")
                name, value = name_value.split(":", 1)
                if not name.startswith("ai_gateway."):
                    continue
                assert kind == "c", line  # Usage must sum, not overwrite a gauge.
                assert not any(field.startswith("@") for field in fields), line
                tags = next(field[1:] for field in fields if field.startswith("#"))
                self.packets.append((name, float(value), tuple(sorted(tags.split(",")))))

    def snapshot(self):
        return len(self.packets)

    def groups(self, since=0):
        counters = defaultdict(lambda: defaultdict(float))
        for name, value, tags in list(self.packets)[since:]:
            counters[tags][name] += value
        return [
            {"tags": dict(tag.split(":", 1) for tag in tags), "counters": dict(values)}
            for tags, values in counters.items()
        ]

    async def wait(self, requests, since=0):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            groups = self.groups(since)
            if sum(group["counters"].get("ai_gateway.requests", 0) for group in groups) >= requests:
                return groups
            await asyncio.sleep(0.1)
        pytest.fail(f"Missing usage metrics: {self.groups(since)}")

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=5)
        self.socket.close()


@pytest.fixture(scope="module")
def gateway(tmp_path_factory, request):
    temp = tmp_path_factory.mktemp("gateway")
    mode = getattr(request, "param", None)
    metrics = Metrics()
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
                metrics.traces.extend(msgpack.unpackb(body, raw=False, strict_map_key=False))
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
            retry_failure = (
                data["model"] == "gpt-4o-retry"
                and sum(r["model"] == "gpt-4o-retry" for r in provider_requests) % 2 == 1
            )
            if data["model"] == "gpt-4o-failing" or retry_failure:
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
            if mode == "context_buckets":
                # The synthetic provider reports different usage for a small payload.
                usage.update(prompt_tokens=data["seed"], total_tokens=data["seed"] + 25)
            if data["model"] == "gpt-4o-modal":
                usage["prompt_tokens_details"] = {
                    "text_tokens": 70,
                    "image_tokens": 30,
                    "cached_tokens": 5,
                    "audio_length_seconds": 0.25,
                }
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
        ("retry-model", "gpt-4o-retry", "retry-deployment"),
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
    if mode == "missing_key_id":
        for model in models:
            model["model_info"].pop("datadog_provider_api_key_id", None)
    if mode == "retries":
        config["router_settings"].update(
            num_retries=1,
            retry_after=0,
            retry_policy={"InternalServerErrorRetries": 1},
            fallbacks=[{"fail-model": ["retry-model"]}],
        )
    if mode == "disabled":
        config["litellm_settings"]["callbacks"] = []
    if mode == "gateway_cache":
        config["litellm_settings"].update(cache=True, cache_params={"type": "local"})
    config_path.write_text(yaml.safe_dump(config))
    # Inherit only OS/runtime necessities, never real cloud/API/Datadog credentials.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT") if key in os.environ}
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "DD_TRACE_AGENT_URL": local,
            "DD_DOGSTATSD_URL": f"udp://127.0.0.1:{metrics.socket.getsockname()[1]}",
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
                "auth_metadata_keys": ["cost_center"],
            }
        )
    )
    env["DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG"] = str(attribution_config)
    if mode not in (None, "missing_key_id"):
        # Exercise the documented ddtrace-run setup alongside normal SDK tracing.
        for integration in ("LITELLM", "OPENAI", "ANTHROPIC"):
            env.pop(f"DD_TRACE_{integration}_ENABLED")
        if mode in ("default_setup", "disabled", "gateway_cache"):
            env.pop("DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG")
        elif mode == "privacy_opt_out":
            attribution_config.write_text(json.dumps({"capture_email": False, "capture_end_user": False}))
        elif mode == "invalid_config":
            attribution_config.write_text('{"capture_email": false,')
        elif mode == "unreadable_config":
            attribution_config.unlink()
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
    if mode in ("metrics_only", "context_buckets"):
        command.pop(0)
        env["DD_TRACE_ENABLED"] = "false"
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
        yield url, metrics, provider_requests, temp / "proxy.log"
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
        metrics.close()


async def test_real_proxy_and_wire_metrics(gateway):
    url, metrics, upstream, _ = gateway

    async def request(user, model="test-model", stream=False):
        data = {
            "model": model,
            "messages": [{"role": "user", "content": "PRIVATE PROMPT"}],
            "stream": stream,
            "service_tier": "priority",
            "user": "claimed-user",
            "model_info": {"datadog_provider_api_key_id": "spoofed-key"},
            "standard_logging_object": {"model": "SPOOFED MODEL", "cache_hit": True},
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
    groups = await metrics.wait(4)
    assert len(groups) == 4, groups
    tags = [s["tags"] for s in groups]
    assert sorted(t["usr.id"] for t in tags) == ["alice", "alice", "bob", "bob"]
    assert all(t["usr.email"] == f"{t['usr.id']}_example.test" for t in tags)
    assert all(t["ai.enrichment.cost_center"] == "test-eng" for t in tags)
    successful = [s for s in groups if s["tags"]["ai.request.outcome"] == "success"]
    assert len(successful) == 3
    assert sorted(s["tags"]["ai.gateway.deployment_id"] for s in successful) == [
        "fallback-deployment",
        "openai-deployment",
        "openai-deployment",
    ]
    assert all(not any(key.startswith("ai.billing.") for key in group["tags"]) for group in groups)
    for group in successful:
        # The OpenAI-shaped mock has no cache-write counter. Preserve totals and
        # cache reads without silently fabricating a complete uncached partition.
        assert "ai_gateway.usage.input_uncached_tokens" not in group["counters"]
        assert group["counters"]["ai_gateway.observed.input_tokens"] == 100
        assert group["counters"]["ai_gateway.observed.input_cache_write_reported"] == 0
        assert "cache_write_detail_missing" in group["tags"]["ai.attribution.issues"]
        assert group["counters"]["ai_gateway.usage.input_cache_read_tokens"] == 40
        assert group["counters"]["ai_gateway.usage.output_tokens"] == 25
        assert group["counters"]["ai_gateway.observed.context_tokens"] == 100
        assert group["tags"]["ai.route.provider"] == "openai"
        assert group["tags"]["ai.route.endpoint_host"] == "127.0.0.1"
        assert group["tags"]["ai.route.api_key_id"] == f"key_{group['tags']['ai.gateway.deployment_id']}"
        assert group["tags"]["ai.response.openai_organization"] == "org-openai-response"
        assert group["tags"]["ai.response.openai_project"] == "proj-openai-response"
        assert "ai.response.anthropic_workspace_id" not in group["tags"]
        if group["tags"]["usr.id"] == "alice":
            assert "ai.response.x_request_id" not in group["tags"]
            assert group["tags"]["ai.observed.service_tier"] == "Future_Response_Tier"
        assert group["tags"]["ai.route.organization"] == "org-router"
        assert group["tags"]["ai.route.project"] == "proj-router"
        assert group["tags"]["ai.request.service_tier"] == "priority"
        assert group["tags"]["ai.effective.service_tier"] == "priority"
    fallback = next(g for g in successful if g["tags"]["ai.gateway.deployment_id"] == "fallback-deployment")
    assert fallback["counters"]["ai_gateway.observed.fallbacks"] == 1
    assert fallback["counters"]["ai_gateway.observed.retries"] == 0
    serialized = json.dumps(groups)
    for group in groups:
        assert group["tags"]["usr.id"] in ("alice", "bob")
        assert group["tags"]["ai.identity.source"] == "gateway_auth"
        assert group["tags"]["ai.end_user.id"] == "claimed-user"
        assert group["tags"]["ai.end_user.trust"] == "unverified"
    for secret in (
        "PRIVATE",
        "PRIVATE OUTPUT",
        "PRIVATE COOKIE",
        "SYNTHETIC-PROVIDER-SECRET",
        "SPOOFED",
        "spoofed",
    ):
        assert secret not in serialized
    assert all("ai.request.id" not in s["tags"] and "ai.response.id" not in s["tags"] for s in groups)
    assert not any(s.get("name") == "ai_gateway.usage" for t in metrics.traces for s in t)
    assert len(upstream) == 5  # 3 successes + initial fallback failure + final error
    assert all(request["observed_project_header"] == "proj-router" for request in upstream)


@pytest.mark.parametrize("stream", [False, True])
async def test_native_coding_agent_endpoints(gateway, stream):
    url, metrics, _, _ = gateway
    before = metrics.snapshot()
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
    groups = await metrics.wait(2, since=before)
    assert len(groups) == 2, groups
    assert {s["tags"]["ai.gateway.deployment_id"] for s in groups} == {
        "openai-deployment",
        "anthropic-deployment",
    }
    for s in groups:
        if s["tags"]["ai.operation"] == "anthropic_messages":
            assert s["counters"]["ai_gateway.usage.input_uncached_tokens"] == 60, s
            assert s["tags"]["ai.route.api_key_id"] == "apikey_anthropic"
            assert s["tags"]["ai.response.anthropic_organization_id"] == "org-anthropic-response"
            assert s["tags"]["ai.response.anthropic_workspace_id"] == "wrkspc-response"
        else:
            assert s["tags"]["ai.route.api_key_id"] == "key_openai-deployment"
            assert s["tags"]["ai.response.openai_organization"] == "org-openai-response"
            assert s["tags"]["ai.response.openai_project"] == "proj-openai-response"
            assert "ai_gateway.usage.input_uncached_tokens" not in s["counters"]
            assert s["counters"]["ai_gateway.observed.input_tokens"] == 100
            assert "cache_write_detail_missing" in s["tags"]["ai.attribution.issues"]
        assert s["counters"]["ai_gateway.usage.input_cache_read_tokens"] == 40
        assert s["counters"]["ai_gateway.usage.output_tokens"] == 25
        assert s["tags"]["usr.id"] == ("alice" if s["tags"]["ai.operation"] == "anthropic_messages" else "bob")
        if s["tags"]["ai.operation"] == "anthropic_messages":
            assert s["tags"]["ai.request.prompt_cache_ttls"] == "1h"
            assert s["tags"]["ai.effective.prompt_cache_ttls"] == "1h"
            assert s["tags"]["ai.effective.max_tokens"] == "100"


async def test_embeddings_and_multimodal_wire_counters(gateway):
    url, metrics, _, _ = gateway
    before = metrics.snapshot()
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
    groups = await metrics.wait(2, since=before)
    assert len(groups) == 2, groups
    embedding = next(s for s in groups if s["tags"]["usr.id"] == "alice")
    assert embedding["tags"]["ai.operation"] in ("embedding", "aembedding")
    assert embedding["tags"]["ai.effective.dimensions"] == "2"
    assert embedding["counters"]["ai_gateway.usage.input_uncached_tokens"] == 12
    modal = next(s for s in groups if s["tags"]["usr.id"] == "bob")
    assert modal["counters"]["ai_gateway.observed.input_audio_length_seconds"] == 0.25
    assert modal["counters"]["ai_gateway.observed.input_image_tokens"] == 30
    assert modal["counters"]["ai_gateway.observed.input_text_tokens"] == 70
    assert modal["counters"]["ai_gateway.observed.input_cache_read_tokens"] == 5
    assert "ai_gateway.usage.input_uncached_tokens" not in modal["counters"]
    assert "ai.billing.provider" not in modal["tags"]
    assert "PRIVATE" not in json.dumps(groups)


async def test_bedrock_model_id_survives_real_router_and_provider_hooks(gateway):
    url, metrics, upstream, _ = gateway
    before = metrics.snapshot()
    async with httpx.AsyncClient(timeout=20) as client:
        result = await client.post(
            f"{url}/chat/completions",
            headers={"Authorization": "Bearer test-alice"},
            json={"model": "bedrock-profile", "messages": [{"role": "user", "content": "PRIVATE PROMPT"}]},
        )
    assert result.status_code == 200, result.text
    groups = await metrics.wait(1, since=before)
    assert len(groups) == 1
    group = groups[0]
    assert group["tags"]["ai.route.model_id"] == BEDROCK_PROFILE
    assert group["tags"]["ai.route.aws_region_name"] == "us-east-1"
    assert "ai.billing.account_id" not in group["tags"]
    assert "ai.billing.provider" not in group["tags"]
    assert any(BEDROCK_PROFILE in unquote(request["observed_path"]) for request in upstream)
    for private in ("PRIVATE", "SYNTHETIC-AWS-ACCESS", "SYNTHETIC-AWS-SECRET"):
        assert private not in json.dumps(groups)


@pytest.mark.parametrize("stream", [False, True])
async def test_raw_azure_claude_route_reaches_wire(gateway, stream):
    url, metrics, upstream, _ = gateway
    before = metrics.snapshot()
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
    groups = await metrics.wait(1, since=before)
    assert len(groups) == 1
    group = groups[0]
    assert group["tags"]["usr.id"] == "alice"
    assert group["tags"]["ai.route.provider"] == "azure_ai"
    assert not any(key.startswith("ai.billing.") for key in group["tags"])
    assert group["counters"]["ai_gateway.observed.context_tokens"] == 100
    assert group["counters"]["ai_gateway.usage.input_cache_read_tokens"] == 40
    assert group["counters"]["ai_gateway.usage.output_tokens"] == 25
    assert any(request["observed_path"] == "/anthropic/v1/messages" for request in upstream)
    assert "PRIVATE" not in json.dumps(groups)
    assert "SYNTHETIC-AZURE-SECRET" not in json.dumps(groups)


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
    url, metrics, _, _ = gateway
    before = metrics.snapshot()
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
    groups = await metrics.wait(1, since=before)
    assert len(groups) == 1, groups
    tags = groups[0]["tags"]
    assert tags.get("usr.id") == expected
    assert tags.get("ai.end_user.id") == expected
    assert tags["ai.identity.source"] == ("litellm_end_user" if expected else "unknown")
    assert tags.get("ai.end_user.trust") == ("unverified" if expected else None)
    assert tags["ai.attribution.status"] == "incomplete"
    assert "authenticated_user_unknown" in tags["ai.attribution.issues"]
    assert groups[0]["counters"]["ai_gateway.usage.output_tokens"] == 25
    assert not any(value in json.dumps(groups) for value in ("PRIVATE", "SYNTHETIC", "ignored-", "test-unassigned"))


@pytest.mark.parametrize("gateway", ["missing_key_id"], indirect=True)
async def test_missing_key_id_warns_but_real_proxy_still_exports_usage(gateway):
    url, metrics, _, log_path = gateway
    async with httpx.AsyncClient(timeout=20) as client:
        for model in ("test-model", "fail-model"):
            result = await client.post(
                f"{url}/v1/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": "PRIVATE"}]},
                headers={"Authorization": "Bearer test-alice"},
            )
            assert result.status_code == 200, result.text
    groups = await metrics.wait(2)
    assert len(groups) == 2
    for group in groups:
        assert "ai.route.api_key_id" not in group["tags"]
        assert group["tags"]["usr.id"] == "alice"
        assert group["counters"]["ai_gateway.observed.context_tokens"] > 0
    warnings = [line for line in log_path.read_text().splitlines() if "LiteLLM gateway usage is missing" in line]
    assert len(warnings) == 1
    assert "model_info.datadog_provider_api_key_id" in warnings[0]
    assert "non-secret key ID" in warnings[0]
    assert "PRIVATE" not in warnings[0] and "SYNTHETIC" not in warnings[0]


@pytest.mark.parametrize("gateway", ["default_setup"], indirect=True)
async def test_documented_setup_with_sdk_tracing_and_concurrent_requests(gateway):
    url, metrics, upstream, _ = gateway
    expected = {}
    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=40) as client:

        async def send(index):
            user = "alice" if (index // 3) % 2 else "bob"
            trace_id = 700000 + index
            end_user = f"end-user-{index}"
            endpoint = ("chat/completions", "messages", "responses")[index % 3]
            stream = bool((index // 6) % 2)
            data = {"model": "test-model", "stream": stream}
            if endpoint == "responses":
                data.update(input="PRIVATE PROMPT", safety_identifier=end_user)
            else:
                data["messages"] = [{"role": "user", "content": "PRIVATE PROMPT"}]
                if endpoint == "messages":
                    data.update(model="test-claude", max_tokens=100)
                else:
                    data["user"] = end_user
                    if stream:
                        data["stream_options"] = {"include_usage": True}
            expected[end_user] = (user, end_user, "anthropic" if endpoint == "messages" else "openai")
            async with semaphore:
                result = await client.post(
                    f"{url}/v1/{endpoint}",
                    json=data,
                    headers={
                        "Authorization": f"Bearer test-{user}",
                        "anthropic-version": "2023-06-01",
                        "x-litellm-end-user-id": end_user,
                        "x-datadog-trace-id": str(trace_id),
                        "x-datadog-parent-id": str(800000 + index),
                        "x-datadog-sampling-priority": "2",
                    },
                )
                assert result.status_code == 200, result.text

        await asyncio.gather(*(send(index) for index in range(24)))
    groups = await metrics.wait(len(expected))
    await asyncio.sleep(1)  # Also catch duplicate terminal callbacks and SDK trace delivery.
    groups = metrics.groups()
    all_spans = [s for t in list(metrics.traces) for s in t]
    assert len(groups) == len(expected) == len(upstream)
    assert sum(g["counters"]["ai_gateway.requests"] for g in groups) == len(expected)
    assert any(s["name"].startswith("litellm.") for s in all_spans)
    assert any(s["name"].startswith("openai.") for s in all_spans)
    assert not any(s["name"] == "ai_gateway.usage" for s in all_spans)
    for group in groups:
        user, end_user, provider = expected[group["tags"]["ai.end_user.id"]]
        tags = group["tags"]
        assert tags["usr.id"] == user
        assert tags["usr.email"] == f"{user}_example.test"
        assert tags["ai.identity.source"] == "gateway_auth"
        assert tags["ai.end_user.id"] == end_user
        assert tags["ai.end_user.trust"] == "unverified"
        assert tags["ai.route.provider"] == provider
        assert tags["ai.route.api_key_id"] == (
            "apikey_anthropic" if provider == "anthropic" else "key_openai-deployment"
        )
        assert not any(key.startswith("ai.enrichment.") for key in tags)
        assert group["counters"]["ai_gateway.usage.output_tokens"] == 25
    assert not any(value in json.dumps(groups) for value in ("PRIVATE", "SYNTHETIC", "Bearer test-"))


@pytest.mark.parametrize("gateway", ["disabled"], indirect=True)
async def test_upgrade_without_callback_does_not_emit_gateway_metrics(gateway):
    url, metrics, upstream, _ = gateway
    async with httpx.AsyncClient(timeout=20) as client:
        result = await client.post(
            f"{url}/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "PRIVATE PROMPT"}]},
            headers={"Authorization": "Bearer test-alice"},
        )
    assert result.status_code == 200, result.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any(s["name"].startswith("litellm.") for t in list(metrics.traces) for s in t):
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    groups = [s for t in list(metrics.traces) for s in t]
    assert not metrics.packets
    assert len(upstream) == 1
    assert any(s["name"].startswith("litellm.") for s in groups)
    assert not any(s["name"] == "ai_gateway.usage" for s in groups)


@pytest.mark.parametrize("gateway", ["privacy_opt_out", "invalid_config", "unreadable_config"], indirect=True)
async def test_privacy_settings_through_real_proxy(gateway):
    url, metrics, upstream, _ = gateway
    async with httpx.AsyncClient(timeout=20) as client:
        for user in ("alice", "unassigned"):
            result = await client.post(
                f"{url}/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "PRIVATE"}],
                    "user": "opted-out-user",
                },
                headers={"Authorization": f"Bearer test-{user}"},
            )
            assert result.status_code == 200, result.text
    groups = await metrics.wait(2)
    assert len(groups) == len(upstream) == 2
    assert {s["tags"].get("usr.id") for s in groups} == {"alice", None}
    for group in groups:
        tags = group["tags"]
        assert "usr.email" not in tags
        assert "ai.end_user.id" not in tags
        assert not any(key.startswith("ai.enrichment.") for key in tags)
        assert group["counters"]["ai_gateway.usage.output_tokens"] == 25
    assert "opted-out-user" not in json.dumps(groups)


@pytest.mark.parametrize("gateway", ["gateway_cache"], indirect=True)
async def test_gateway_cache_hit_preserves_current_user_without_new_provider_usage(gateway):
    url, metrics, upstream, _ = gateway
    async with httpx.AsyncClient(timeout=20) as client:
        for user in ("alice", "bob"):
            result = await client.post(
                f"{url}/v1/chat/completions",
                json={"model": "test-model", "messages": [{"role": "user", "content": "CACHE TEST"}]},
                headers={"Authorization": f"Bearer test-{user}"},
            )
            assert result.status_code == 200, result.text
    groups = await metrics.wait(2)
    assert len(groups) == 2
    assert len(upstream) == 1
    by_user = {s["tags"]["usr.id"]: s for s in groups}
    assert set(by_user) == {"alice", "bob"}
    assert by_user["alice"]["tags"]["ai.request.outcome"] == "success"
    cached = by_user["bob"]
    assert cached["tags"]["ai.request.outcome"] == "gateway_cache_hit"
    assert cached["tags"]["ai.usage.source"] == "gateway_cache"
    assert cached["tags"]["ai.context_tokens.bucket"] == "unknown"
    assert not any(key.startswith("ai_gateway.usage.") for key in cached["counters"])


@pytest.mark.parametrize("gateway", ["metrics_only"], indirect=True)
async def test_metrics_without_tracing_aggregate_identical_attribution(gateway):
    url, metrics, upstream, _ = gateway
    async with httpx.AsyncClient(timeout=40) as client:
        results = await asyncio.gather(
            *[
                client.post(
                    f"{url}/v1/chat/completions",
                    json={"model": "test-model", "messages": [{"role": "user", "content": "PRIVATE"}]},
                    headers={"Authorization": "Bearer test-alice"},
                )
                for _ in range(12)
            ]
        )
    assert all(r.status_code == 200 for r in results)
    groups = await metrics.wait(12)
    await asyncio.sleep(0.5)
    assert groups == metrics.groups()
    assert len(groups) == 1  # No request-specific tags split the metric series.
    group = groups[0]
    assert group["tags"]["usr.id"] == "alice"
    assert group["tags"]["usr.email"] == "alice_example.test"
    assert group["tags"]["service"] == "gateway-attribution-test"
    assert group["tags"]["env"] == "local-test"
    assert group["tags"]["version"] == "test"
    assert group["tags"]["ai.context_tokens.bucket"] == "0_32000"
    assert group["counters"]["ai_gateway.requests"] == len(upstream) == 12
    assert group["counters"]["ai_gateway.usage.output_tokens"] == 12 * 25
    assert group["counters"]["ai_gateway.observed.retries"] == 0
    assert group["counters"]["ai_gateway.observed.fallbacks"] == 0
    assert not metrics.traces
    assert not any("cost" in name or "duration" in name for name in group["counters"])


@pytest.mark.parametrize("gateway", ["context_buckets"], indirect=True)
async def test_context_buckets_separate_metric_totals_without_model_rules(gateway):
    url, metrics, upstream, _ = gateway
    cases = [
        (31_999, "0_32000"),
        (32_000, "0_32000"),
        (32_001, "32001_128000"),
        (128_000, "32001_128000"),
        (128_001, "128001_200000"),
        (200_000, "128001_200000"),
        (200_001, "200001_256000"),
        (256_000, "200001_256000"),
        (256_001, "256001_272000"),
        (272_000, "256001_272000"),
        (272_001, "272001_512000"),
        (512_000, "272001_512000"),
        (512_001, "512001_plus"),
    ]
    async with httpx.AsyncClient(timeout=40) as client:
        for tokens, _ in cases:
            result = await client.post(
                f"{url}/v1/chat/completions",
                json={"model": "test-model", "seed": tokens, "messages": [{"role": "user", "content": "PRIVATE"}]},
                headers={"Authorization": "Bearer test-alice"},
            )
            assert result.status_code == 200
    groups = await metrics.wait(len(cases))
    assert len(groups) == 7
    assert len(upstream) == len(cases)
    assert not metrics.traces
    for group in groups:
        reported = [tokens for tokens, bucket in cases if bucket == group["tags"]["ai.context_tokens.bucket"]]
        assert group["counters"]["ai_gateway.requests"] == len(reported)
        assert group["counters"]["ai_gateway.observed.context_tokens"] == sum(reported)
        assert group["counters"]["ai_gateway.usage.output_tokens"] == 25 * len(reported)


@pytest.mark.parametrize("gateway", ["retries"], indirect=True)
@pytest.mark.parametrize("stream", [False, True])
async def test_observed_retries_across_fallbacks_and_terminal_failure(gateway, stream):
    url, metrics, upstream, _ = gateway
    before = metrics.snapshot()
    upstream_before = len(upstream)
    async with httpx.AsyncClient(timeout=40) as client:
        for model, status in (("fail-model", 200), ("error-model", 503)):
            result = await client.post(
                f"{url}/v1/chat/completions",
                json={
                    "model": model,
                    "stream": stream,
                    "stream_options": {"include_usage": True},
                    "messages": [{"role": "user", "content": "PRIVATE"}],
                },
                headers={"Authorization": "Bearer test-alice"},
            )
            assert result.status_code == status, result.text
    groups = await metrics.wait(2, since=before)
    assert len(upstream) - upstream_before == 6  # Two failed primary + two fallback + two final failures.
    success = next(g for g in groups if g["tags"]["ai.request.outcome"] == "success")
    failure = next(g for g in groups if g["tags"]["ai.request.outcome"] == "error")
    assert success["counters"]["ai_gateway.observed.retries"] == 2
    assert success["counters"]["ai_gateway.observed.fallbacks"] == 1
    assert success["counters"]["ai_gateway.observed.attempts"] == 4
    assert success["counters"]["ai_gateway.usage.output_tokens"] == 25
    assert failure["counters"]["ai_gateway.observed.retries"] == 1
    assert failure["counters"]["ai_gateway.observed.fallbacks"] == 0
    assert failure["counters"]["ai_gateway.observed.attempts"] == 2
    assert not any(key.startswith("ai_gateway.usage.") for key in failure["counters"])
