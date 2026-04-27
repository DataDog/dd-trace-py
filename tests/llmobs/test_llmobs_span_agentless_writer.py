from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import os
import threading
import time

import mock
import pytest

from ddtrace.internal.settings import env
from ddtrace.llmobs._constants import AGENTLESS_SPAN_BASE_URL
from ddtrace.llmobs._constants import SPAN_ENDPOINT
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _completion_event
from tests.llmobs._utils import _large_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event
from tests.utils import override_global_config


DD_SITE = "datad0g.com"
DD_API_KEY = env.get("DD_API_KEY", default="<not-a-real-api-key>")
INTAKE_URL = f"{AGENTLESS_SPAN_BASE_URL}.{DD_SITE}{SPAN_ENDPOINT}"


def test_writer_start(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    llmobs_span_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsSpanWriter", INTAKE_URL)])
    llmobs_span_writer.stop()


def test_buffer_limit(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    for _ in range(1001):
        llmobs_span_writer.enqueue({})
    mock_writer_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "LLMObsSpanWriter", 1000
    )


@mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload")
def test_flush_queue_when_event_cause_queue_to_exceed_payload_limit(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls(
        [
            mock.call("manually flushing buffer because queueing next event will exceed EVP payload limit"),
            mock.call("encoded %d LLMObs %s events to be sent", 2, "span"),
            mock.call("encoded %d LLMObs %s events to be sent", 1, "span"),
        ],
        any_order=True,
    )


def test_truncating_oversized_events(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    llmobs_span_writer.enqueue(_oversized_llm_event())
    llmobs_span_writer.enqueue(_oversized_retrieval_event())
    llmobs_span_writer.enqueue(_oversized_workflow_event())
    mock_writer_logs.warning.assert_has_calls(
        [
            mock.call(
                "dropping event input/output because its size (%d) exceeds the event size limit (%d bytes)",
                5200729,
                5000000,
            ),
            mock.call(
                "dropping event input/output because its size (%d) exceeds the event size limit (%d bytes)",
                5200469,
                5000000,
            ),
            mock.call(
                "dropping event input/output because its size (%d) exceeds the event size limit (%d bytes)",
                5200450,
                5000000,
            ),
        ]
    )


@pytest.mark.vcr_logs
def test_send_completion_event(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


@pytest.mark.vcr_logs
def test_send_chat_completion_event(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    llmobs_span_writer.enqueue(_chat_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


def test_send_completion_bad_api_key(mock_writer_logs):
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers["Content-Length"])
            self.rfile.read(content_length)
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'{"errors":["Forbidden"]}')

        def log_message(self, *args):
            pass  # suppress server noise in test output

    server = HTTPServer(("localhost", 0), _Handler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    mock_url = f"http://localhost:{server.server_address[1]}"

    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _override_url=mock_url, _api_key="<bad-api-key>")
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()

    server.shutdown()
    server.server_close()

    mock_writer_logs.error.assert_called_with(
        "failed to send %d LLMObs %s events to %s, got response code %d, status: %s",
        1,
        "span",
        f"{mock_url}/api/v2/llmobs",
        403,
        b'{"errors":["Forbidden"]}',
        extra={"send_to_telemetry": False},
    )


def test_send_completion_no_api_key(mock_writer_logs):
    with override_global_config(dict(_dd_api_key="")):
        llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=True, _site=DD_SITE, _api_key="")
        llmobs_span_writer.enqueue(_completion_event())
        llmobs_span_writer.periodic()
    mock_writer_logs.warning.assert_called_with(
        "A Datadog API key is required for sending data to LLM Observability in agentless mode. "
        "LLM Observability data will not be sent. Ensure an API key is set either via DD_API_KEY or via "
        "`LLMObs.enable(api_key=...)` before running your application."
    )


@pytest.mark.vcr_logs
def test_send_timed_events(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(0.01, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    llmobs_span_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_span_writer.enqueue(_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])
    mock_writer_logs.reset_mock()
    llmobs_span_writer.enqueue(_chat_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


@pytest.mark.vcr_logs
def test_send_multiple_events(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    mock_writer_logs.reset_mock()

    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.enqueue(_chat_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 2, "span")])


def test_send_on_exit(run_python_code_in_subprocess):
    requests_received = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers["Content-Length"])
            requests_received.append(self.rfile.read(content_length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass  # suppress server noise in test output

    server = HTTPServer(("localhost", 0), _Handler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    mock_url = f"http://localhost:{server.server_address[1]}"

    subenv = env.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(__file__)))]
    if "PYTHONPATH" in env:
        pypath.append(subenv["PYTHONPATH"])
    subenv.update({"PYTHONPATH": ":".join(pypath), "DD_LLMOBS_OVERRIDE_ORIGIN": mock_url})

    out, err, status, pid = run_python_code_in_subprocess(
        """
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs.test_llmobs_span_agentless_writer import _completion_event

llmobs_span_writer = LLMObsSpanWriter(1000, 1, is_agentless=True, _api_key="<not-a-real-key>")
llmobs_span_writer.start()
llmobs_span_writer.enqueue(_completion_event())
""",
        env=subenv,
    )

    server.shutdown()
    server.server_close()

    assert status == 0, err
    assert out == b""
    assert len(requests_received) == 1


@mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload")
def test_configurable_payload_size_limit(mock_send_payload, mock_writer_logs):
    """DD_LLMOBS_PAYLOAD_SIZE_BYTES overrides the flush threshold."""
    with override_global_config(dict(_llmobs_payload_size_limit=100)):
        llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
        llmobs_span_writer.enqueue(_completion_event())
        llmobs_span_writer.enqueue(_completion_event())
    mock_writer_logs.debug.assert_any_call(
        "manually flushing buffer because queueing next event will exceed EVP payload limit"
    )


def test_configurable_event_size_limit(mock_writer_logs):
    """DD_LLMOBS_EVENT_SIZE_BYTES overrides the truncation threshold."""
    with override_global_config(dict(_llmobs_event_size_limit=100)):
        llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
        llmobs_span_writer.enqueue(_completion_event())
    mock_writer_logs.warning.assert_called_once_with(
        "dropping event input/output because its size (%d) exceeds the event size limit (%d bytes)",
        mock.ANY,
        100,
    )
