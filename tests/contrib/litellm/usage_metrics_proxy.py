"""A LiteLLM proxy under ddtrace-run, a fake model provider, and collectors for the metrics it exports.

The fake provider answers like OpenAI and Anthropic with fixed usage numbers, and can fail on purpose:
a model name containing ``fail429`` or ``fail500`` always fails, ``flaky`` fails every other call with a 429,
and ``nousage`` streams without usage. Nothing leaves localhost.
"""

import collections
import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request


OPENAI_USAGE = {
    "prompt_tokens": 12,
    "completion_tokens": 7,
    "total_tokens": 19,
    "prompt_tokens_details": {"cached_tokens": 4, "audio_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 3, "audio_tokens": 0},
}
ANTHROPIC_START_USAGE = {
    "input_tokens": 10,
    "cache_creation_input_tokens": 30,
    "cache_read_input_tokens": 20,
    "cache_creation": {"ephemeral_5m_input_tokens": 18, "ephemeral_1h_input_tokens": 12},
    "output_tokens": 1,
}
ANTHROPIC_OUTPUT = 5

# Fake virtual keys of the proxy, and the identity each carries.
KEYS = {
    "sk-alice-0001": {"user_id": "user-alice", "team_id": "team-ml", "key_alias": "alice-key"},
    "sk-bob-0002": {"user_id": "user-bob", "team_id": "team-web"},
}
PROVIDER_KEYS = ("fake-openai-key", "fake-anthropic-key")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _sse(data, event=None):
    payload = data if isinstance(data, str) else json.dumps(data)
    return (f"event: {event}\n" if event else "").encode() + f"data: {payload}\n\n".encode()


class _Provider(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    counters = collections.Counter()
    requests = []

    def log_message(self, *args):
        pass

    def _json(self, status, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, chunks):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(chunk)
            self.wfile.flush()
        self.close_connection = True

    def _fail(self, model, anthropic):
        """Fail as the model name asks. Returns True when it did."""
        status = None
        if "fail429" in model:
            status = 429
        elif "fail500" in model:
            status = 500
        elif "flaky" in model:
            _Provider.counters[model] += 1
            if _Provider.counters[model] % 2 == 1:
                status = 429
        if status is None:
            return False
        kind = "rate_limit_error" if status == 429 else "api_error"
        body = (
            {"type": "error", "error": {"type": kind, "message": "fake"}}
            if anthropic
            else {"error": {"message": "fake", "type": kind, "param": None, "code": kind}}
        )
        self._json(status, body, {"retry-after": "0"})
        return True

    def do_GET(self):
        self._json(200, {"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model", "owned_by": "fake"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        model = body.get("model", "")
        _Provider.requests.append((self.path, model, bool(body.get("stream"))))
        if self.path.endswith("/chat/completions"):
            self._chat(body, model)
        elif self.path.endswith("/embeddings"):
            if not self._fail(model, False):
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
                        "model": model,
                        "usage": {"prompt_tokens": 3, "total_tokens": 3},
                    },
                )
        elif self.path.endswith("/messages"):
            self._messages(body, model)
        else:
            self._json(404, {"error": "not found"})

    def _chat(self, body, model):
        if self._fail(model, False):
            return
        # LiteLLM prices a call by the model its response names, so a model it has no price for answers as itself.
        response_model = f"{model}-2024-07-18"
        base = {"id": "chatcmpl-fake", "created": int(time.time()), "model": response_model}
        if not body.get("stream"):
            self._json(
                200,
                dict(
                    base,
                    object="chat.completion",
                    choices=[
                        {"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
                    ],
                    usage=OPENAI_USAGE,
                ),
            )
            return
        usage = bool((body.get("stream_options") or {}).get("include_usage")) and "nousage" not in model

        def chunk(delta, finish=None, choices=True):
            return dict(
                base,
                object="chat.completion.chunk",
                choices=[{"index": 0, "delta": delta, "finish_reason": finish}] if choices else [],
            )

        chunks = [_sse(chunk({"role": "assistant", "content": ""}))]
        chunks += [_sse(chunk({"content": piece})) for piece in ("hel", "lo")]
        chunks.append(_sse(chunk({}, finish="stop")))
        if usage:
            chunks.append(_sse(dict(chunk({}, choices=False), usage=OPENAI_USAGE)))
        chunks.append(_sse("[DONE]"))
        self._stream(chunks)

    def _messages(self, body, model):
        if self._fail(model, True):
            return
        message = {
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5-20251001",
            "stop_sequence": None,
        }
        if not body.get("stream"):
            usage = dict(ANTHROPIC_START_USAGE, output_tokens=ANTHROPIC_OUTPUT)
            self._json(
                200, dict(message, content=[{"type": "text", "text": "hello"}], stop_reason="end_turn", usage=usage)
            )
            return
        start = dict(message, content=[], stop_reason=None, usage=dict(ANTHROPIC_START_USAGE))
        chunks = [
            _sse({"type": "message_start", "message": start}, "message_start"),
            _sse(
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                "content_block_start",
            ),
        ]
        for piece in ("hel", "lo"):
            delta = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}}
            chunks.append(_sse(delta, "content_block_delta"))
        chunks.append(_sse({"type": "content_block_stop", "index": 0}, "content_block_stop"))
        chunks.append(
            _sse(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": ANTHROPIC_OUTPUT},
                },
                "message_delta",
            )
        )
        chunks.append(_sse({"type": "message_stop"}, "message_stop"))
        self._stream(chunks)


class _OtlpCollector(http.server.BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        _OtlpCollector.bodies.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _TraceCollector(http.server.BaseHTTPRequestHandler):
    """Stands in for the trace agent: keeps every v0.4 trace payload."""

    payloads = []

    def log_message(self, *args):
        pass

    def _reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply({"endpoints": ["/v0.4/traces"]})

    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/v0.4/traces"):
            _TraceCollector.payloads.append(body)
        self._reply({"rate_by_service": {}})

    do_POST = do_PUT


def _serve(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class DogStatsdCollector:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.packets = []
        self._done = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._done:
            try:
                self.packets.append(self.sock.recv(65535))
            except socket.timeout:
                pass

    def stop(self):
        if self._done:
            return [line for packet in self.packets for line in packet.decode().split("\n") if line]
        self._done = True
        self._thread.join()
        # Read what is still queued: the proxy's last flush, sent as it exited.
        self.sock.settimeout(0.5)
        while True:
            try:
                self.packets.append(self.sock.recv(65535))
            except socket.timeout:
                break
        self.sock.close()
        return [line for packet in self.packets for line in packet.decode().split("\n") if line]


def _config(provider_port):
    openai = f"api_base: http://127.0.0.1:{provider_port}/v1, api_key: {PROVIDER_KEYS[0]}"
    anthropic = f"api_base: http://127.0.0.1:{provider_port}, api_key: {PROVIDER_KEYS[1]}"
    return textwrap.dedent(
        f"""
        model_list:
          - model_name: gpt-4o-mini
            litellm_params: {{model: openai/gpt-4o-mini, {openai}}}
          - model_name: gpt-4o-mini-nousage
            litellm_params: {{model: openai/gpt-4o-mini-nousage, {openai}}}
          - model_name: flaky-gpt
            litellm_params: {{model: openai/gpt-4o-mini-flaky, {openai}}}
          - model_name: primary-fails
            litellm_params: {{model: openai/gpt-4o-mini-fail500-primary, {openai}}}
          - model_name: fallback-target
            litellm_params: {{model: openai/gpt-4o-mini, {openai}}}
          - model_name: always-fails
            litellm_params: {{model: openai/gpt-4o-mini-fail500-always, {openai}}}
          - model_name: text-embedding-3-small
            litellm_params: {{model: openai/text-embedding-3-small, {openai}}}
          - model_name: claude-haiku-4-5
            litellm_params: {{model: anthropic/claude-haiku-4-5, {anthropic}}}
          - model_name: custom-unpriced
            litellm_params: {{model: openai/my-custom-model-xyz, {openai}}}
        litellm_settings:
          cache: true
          cache_params: {{type: local, mode: default_off}}
          drop_params: true
        router_settings:
          num_retries: 0
          retry_after: 0
          disable_cooldowns: true
          model_group_retry_policy:
            flaky-gpt: {{RateLimitErrorRetries: 2}}
            always-fails: {{InternalServerErrorRetries: 2}}
          fallbacks:
            - primary-fails: [fallback-target]
        general_settings:
          master_key: sk-master-0000
          custom_auth: usage_metrics_proxy_auth.user_api_key_auth
        """
    )


_AUTH = textwrap.dedent(
    f"""
    from litellm.proxy._types import UserAPIKeyAuth

    KEYS = {KEYS!r}

    async def user_api_key_auth(request, api_key):
        key = (api_key or "").replace("Bearer ", "").strip()
        if key not in KEYS:
            raise Exception("invalid fake key")
        return UserAPIKeyAuth(api_key=key, **KEYS[key])
    """
)


class Proxy:
    """A LiteLLM proxy under ddtrace-run with usage metrics on, in front of the fake provider."""

    def __init__(self, workdir, exporter, provider_port, otlp_port, dogstatsd_port, env_overrides=None):
        self.port = free_port()
        self.workdir = str(workdir)
        with open(os.path.join(self.workdir, "config.yaml"), "w") as f:
            f.write(_config(provider_port))
        with open(os.path.join(self.workdir, "usage_metrics_proxy_auth.py"), "w") as f:
            f.write(_AUTH)
        bindir = os.path.dirname(sys.executable)
        env = dict(os.environ)
        env.update(
            PYTHONPATH=os.pathsep.join([self.workdir, os.getcwd(), env.get("PYTHONPATH", "")]),
            DD_LITELLM_USAGE_METRICS_ENABLED="true",
            DD_LITELLM_USAGE_METRICS_EXPORTER=exporter,
            DD_LITELLM_USAGE_METRICS_TAGS="user,team,key_alias,route,destination",
            OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=f"http://127.0.0.1:{otlp_port}/v1/metrics",
            OTEL_METRIC_EXPORT_INTERVAL="500",
            DD_DOGSTATSD_URL=f"udp://127.0.0.1:{dogstatsd_port}",
            DD_TRACE_AGENT_URL="http://127.0.0.1:9",
            DD_INSTRUMENTATION_TELEMETRY_ENABLED="false",
            DD_REMOTE_CONFIGURATION_ENABLED="false",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
            LITELLM_TELEMETRY="False",
            NO_PROXY="127.0.0.1,localhost",
        )
        env.update(env_overrides or {})
        self.log = open(os.path.join(self.workdir, "proxy.log"), "w")
        command = [
            os.path.join(bindir, "ddtrace-run"),
            os.path.join(bindir, "litellm"),
            "--config",
            "config.yaml",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--num_workers",
            "1",
        ]
        self.process = subprocess.Popen(command, cwd=self.workdir, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"the proxy exited:\n{self.output()}")
            try:
                # Older proxies authenticate the health route too.
                probe = urllib.request.Request(
                    f"http://127.0.0.1:{self.port}/health/liveliness",
                    headers={"Authorization": "Bearer sk-alice-0001"},
                )
                urllib.request.urlopen(probe, timeout=1).read()
                return
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        self.stop()
        raise RuntimeError(f"the proxy did not start:\n{self.output()}")

    def output(self):
        if not self.log.closed:
            self.log.flush()
        with open(os.path.join(self.workdir, "proxy.log")) as f:
            return f.read()[-5000:]

    def request(self, path, body, key="sk-alice-0001"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def chat(self, model, stream=False, key="sk-alice-0001", **extra):
        body = dict(model=model, messages=[{"role": "user", "content": "hi"}], stream=stream, **extra)
        return self.request("/v1/chat/completions", body, key)

    def stop(self):
        """Stop the proxy the way a deployment does, so ddtrace flushes at exit."""
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()


class Gateway:
    """The fake provider and the metric collectors, shared by the proxies of a test."""

    def __init__(self):
        _Provider.counters.clear()
        _Provider.requests = []
        _OtlpCollector.bodies = []
        _TraceCollector.payloads = []
        self.provider = _serve(_Provider)
        self.otlp = _serve(_OtlpCollector)
        self.traces = _serve(_TraceCollector)
        self.dogstatsd = DogStatsdCollector()

    @property
    def provider_requests(self):
        return _Provider.requests

    @property
    def otlp_bodies(self):
        return _OtlpCollector.bodies

    @property
    def trace_payloads(self):
        return _TraceCollector.payloads

    def proxy(self, workdir, exporter):
        trace_agent = {
            "DD_TRACE_AGENT_URL": f"http://127.0.0.1:{self.traces.server_address[1]}",
            "DD_TRACE_API_VERSION": "v0.4",
        }
        return Proxy(
            workdir,
            exporter,
            self.provider.server_address[1],
            self.otlp.server_address[1],
            self.dogstatsd.port,
            env_overrides=trace_agent,
        )

    def close(self):
        self.provider.shutdown()
        self.otlp.shutdown()
        self.traces.shutdown()
        return self.dogstatsd.stop()
