"""
End-to-end test for LLMObs.register_processor() tenant-based span filtering.

Validates that:
- HTV traffic produces LLMObs spans that reach the Datadog intake.
- Non-HTV traffic produces LLMObs spans that are dropped by the registered processor
  and never reach the Datadog intake.

No ddtrace internals are mocked. Two real HTTP servers are spun up:
  1. Fake OpenAI — responds to POST /v1/chat/completions with a minimal valid response.
  2. Fake Datadog intake — captures POST /api/v2/llmobs payloads for assertion.

The "app under test" is a plain function that sets the thread-local tenant identifier
and calls the OpenAI client — no web framework required. This keeps the test
self-contained in the llmobs venv without adding Flask as a dependency.

The writer flush interval is set to 0.1 s via _DD_LLMOBS_WRITER_INTERVAL so tests
stay fast while still exercising the real flush path.
"""

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from typing import Optional

import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs import LLMObsSpan

# ---------------------------------------------------------------------------
# Module-level thread-local used to propagate the per-request tenant identity
# into the span processor (mirrors how a real web framework would do it).
# ---------------------------------------------------------------------------
_request_ctx = threading.local()

# ---------------------------------------------------------------------------
# Writer flush interval — short enough for deterministic tests.
# ---------------------------------------------------------------------------
_FLUSH_INTERVAL = 0.1
_FLUSH_WAIT = _FLUSH_INTERVAL * 5  # generous buffer after issuing flush

# ---------------------------------------------------------------------------
# Span processor invocation counter — thread-safe via a lock.
# Used to detect vacuous "drop" assertions when no span was ever created.
# ---------------------------------------------------------------------------
_processor_call_lock = threading.Lock()
_processor_call_count = 0


def _reset_processor_call_count():
    global _processor_call_count
    with _processor_call_lock:
        _processor_call_count = 0


def _get_processor_call_count() -> int:
    with _processor_call_lock:
        return _processor_call_count


# ---------------------------------------------------------------------------
# Fake OpenAI server
# ---------------------------------------------------------------------------

_OPENAI_CHAT_RESPONSE = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello, world!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible HTTP handler."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(content_length)
        body = json.dumps(_OPENAI_CHAT_RESPONSE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # suppress per-request noise in test output


def _start_fake_openai() -> tuple:
    """Start fake OpenAI server on a free port. Returns (server, base_url)."""
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    port = server.server_address[1]
    return server, "http://127.0.0.1:{}/v1".format(port)


# ---------------------------------------------------------------------------
# Fake Datadog LLMObs intake server
# ---------------------------------------------------------------------------


def _make_dd_intake_handler(received_payloads: queue.Queue):
    """Return an HTTPRequestHandler class that pushes decoded payloads into the queue."""

    class _FakeDDIntakeHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length)

            content_type = self.headers.get("Content-Type", "")
            if "msgpack" in content_type:
                try:
                    import msgpack

                    payload = msgpack.unpackb(raw, raw=False)
                except Exception:
                    payload = raw
            else:
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = raw

            received_payloads.put(payload)
            self.send_response(202)
            self.end_headers()

        def do_GET(self):
            # Some ddtrace internals probe the agent info endpoint; just 404.
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    return _FakeDDIntakeHandler


def _start_fake_dd_intake(received_payloads: queue.Queue) -> tuple:
    """Start fake Datadog intake on a free port. Returns (server, base_url)."""
    handler_cls = _make_dd_intake_handler(received_payloads)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    port = server.server_address[1]
    return server, "http://127.0.0.1:{}".format(port)


# ---------------------------------------------------------------------------
# App under test — no web framework needed
# ---------------------------------------------------------------------------


def _simulate_request(origin_team: str, openai_base_url: str) -> None:
    """
    Simulate a single inbound request from a given tenant.

    Sets the thread-local tenant identifier (as a real WSGI framework would),
    then makes an OpenAI chat completion call. ddtrace auto-instruments the
    OpenAI client and produces an LLMObs span that the registered processor
    will either pass through or drop based on origin_team.
    """
    openai = pytest.importorskip("openai", reason="openai package required for e2e test")

    # Propagate the tenant identity into thread-local so the span processor can read it.
    _request_ctx.origin_team = origin_team

    client = openai.OpenAI(api_key="fake-key", base_url=openai_base_url)
    # This call is auto-instrumented by ddtrace-openai and produces an LLMObs span.
    client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
    )


# ---------------------------------------------------------------------------
# Span processor — drops spans for non-HTV teams
# ---------------------------------------------------------------------------


def _tenant_filter(span: LLMObsSpan) -> Optional[LLMObsSpan]:
    """Drop the span if the current request is not from the HTV team."""
    global _processor_call_count
    with _processor_call_lock:
        _processor_call_count += 1

    origin_team = getattr(_request_ctx, "origin_team", "")
    if origin_team != "htv":
        return None  # signal to LLMObs to drop this span
    return span


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fake_openai_server():
    server, base_url = _start_fake_openai()
    yield base_url
    server.shutdown()
    server.server_close()


@pytest.fixture()
def dd_intake_payloads():
    """Fresh queue for each test so payloads don't bleed between tests."""
    return queue.Queue()


@pytest.fixture()
def fake_dd_intake(dd_intake_payloads):
    server, base_url = _start_fake_dd_intake(dd_intake_payloads)
    yield base_url
    server.shutdown()
    server.server_close()


@pytest.fixture()
def llmobs_enabled(fake_openai_server, fake_dd_intake, monkeypatch):
    """
    Enable LLMObs for one test then cleanly disable it.

    Uses:
      - DD_LLMOBS_OVERRIDE_ORIGIN  → routes agentless writer to fake intake
      - _DD_LLMOBS_WRITER_INTERVAL → short flush cycle for fast tests
    """
    monkeypatch.setenv("DD_LLMOBS_OVERRIDE_ORIGIN", fake_dd_intake)
    monkeypatch.setenv("_DD_LLMOBS_WRITER_INTERVAL", str(_FLUSH_INTERVAL))
    monkeypatch.setenv("DD_REMOTE_CONFIGURATION_ENABLED", "false")

    _reset_processor_call_count()

    LLMObs.enable(
        ml_app="tenant-filter-e2e",
        agentless_enabled=True,
        api_key="fake-api-key",
        site="datadoghq.com",
        integrations_enabled=True,
        span_processor=_tenant_filter,
    )

    yield

    LLMObs.disable()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain_payloads(payloads_queue: queue.Queue, wait: float = _FLUSH_WAIT) -> list:
    """
    Wait up to `wait` seconds for at least one payload to arrive, then drain
    everything currently queued. Returns a flat list of all span events found.

    The LLMObsSpanWriter posts a JSON array where each element is a dict with
    a "spans" key containing one span event:

        [{"_dd.stage": "raw", ..., "spans": [<span_event>]}, ...]
    """
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not payloads_queue.empty():
            break
        time.sleep(0.02)

    spans = []
    while not payloads_queue.empty():
        try:
            payload = payloads_queue.get_nowait()
        except queue.Empty:
            break
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    spans.extend(item.get("spans", []))
        elif isinstance(payload, dict):
            ml_obs = payload.get("ml_obs") or {}
            spans.extend(ml_obs.get("spans", []))
            spans.extend(payload.get("spans", []))
    return spans


def _wait_no_payloads(payloads_queue: queue.Queue, wait: float = _FLUSH_WAIT) -> bool:
    """Wait `wait` seconds and return True if NO payload arrived during that time."""
    LLMObs.flush()
    time.sleep(wait)
    return payloads_queue.empty()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_htv_traffic_sends_llmobs_span(fake_openai_server, llmobs_enabled, dd_intake_payloads):
    """
    When origin_team is 'htv', the span processor returns the span unchanged.
    The agentless writer must deliver at least one LLMObs span event to the
    fake Datadog intake.
    """
    _simulate_request("htv", fake_openai_server)

    LLMObs.flush()

    spans = _drain_payloads(dd_intake_payloads)
    assert len(spans) >= 1, (
        "Expected at least one LLMObs span to reach the fake intake for HTV traffic, "
        "but intake received nothing after flush."
    )


def test_hdm_traffic_drops_llmobs_span(fake_openai_server, llmobs_enabled, dd_intake_payloads):
    """
    When origin_team is 'hdm', the span processor returns None, signalling
    LLMObs to drop the span. The fake Datadog intake must receive NO LLMObs
    span events after a full flush cycle.
    """
    _simulate_request("hdm", fake_openai_server)

    LLMObs.flush()

    # Guard against vacuous assertion: ensure the processor was actually invoked.
    # If the OpenAI patch silently failed, no span would ever be created, the queue
    # would stay empty, and the "drop" assertion below would prove nothing.
    processor_calls = _get_processor_call_count()
    assert processor_calls >= 1, (
        "Span processor was never called — no LLM span was generated; "
        "the 'drop' assertion is vacuous. Check that the OpenAI integration "
        "is active and that LLMObs.enable() registered the span_processor correctly."
    )

    no_payload = _wait_no_payloads(dd_intake_payloads)
    assert no_payload, (
        "Expected NO LLMObs spans to reach the fake intake for HDM (non-HTV) traffic, "
        "but the intake received at least one payload. The span processor filter may not be working."
    )
