from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import os
import threading
import time

import mock
import pytest

from ddtrace.llmobs._writer import LLMObsEvalMetricWriter
from ddtrace.llmobs._writer import LLMObsEvaluationMetricEvent
from tests.utils import override_global_config


DD_SITE = "datad0g.com"
INTAKE_ENDPOINT = "https://api.datad0g.com/api/intake/llm-obs/v2/eval-metric"
DD_API_KEY = os.getenv("DD_API_KEY", default="<not-a-real-api-key>")


def _categorical_metric_event(label: str, value: str) -> LLMObsEvaluationMetricEvent:
    return {
        "join_on": {
            "span": {
                "span_id": "12345678901",
                "trace_id": "98765432101",
            },
        },
        "metric_type": "categorical",
        "categorical_value": value,
        "label": label,
        "ml_app": "dummy-ml-app",
        "timestamp_ms": 1756910127022,
    }


def _score_metric_event(label: str, value: float) -> LLMObsEvaluationMetricEvent:
    return {
        "join_on": {
            "span": {
                "span_id": "12345678902",
                "trace_id": "98765432102",
            },
        },
        "metric_type": "score",
        "label": label,
        "score_value": value,
        "ml_app": "dummy-ml-app",
        "timestamp_ms": 1756910127022,
    }


def test_writer_start(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    llmobs_eval_metric_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsEvalMetricWriter", INTAKE_ENDPOINT)])
    llmobs_eval_metric_writer.stop()


def test_buffer_limit(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    for _ in range(1001):
        llmobs_eval_metric_writer.enqueue({})
    mock_writer_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "LLMObsEvalMetricWriter", 1000
    )


def test_send_metric_bad_api_key(mock_writer_logs):
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

    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
        interval=1,
        timeout=1,
        is_agentless=True,
        _override_url=mock_url,
        _api_key="<bad-api-key>",
    )
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event(label="api-key", value="wrong-api-key"))
    llmobs_eval_metric_writer.periodic()

    server.shutdown()
    server.server_close()

    mock_writer_logs.error.assert_called_with(
        "failed to send %d LLMObs %s events to %s, got response code %d, status: %s",
        1,
        "evaluation_metric",
        f"{mock_url}/api/intake/llm-obs/v2/eval-metric",
        403,
        b'{"errors":["Forbidden"]}',
        extra={"send_to_telemetry": False},
    )


def test_send_metric_no_api_key(mock_writer_logs):
    with override_global_config(dict(_dd_api_key="")):
        llmobs_eval_metric_writer = LLMObsEvalMetricWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key="")
        llmobs_eval_metric_writer.enqueue(_categorical_metric_event(label="toxicity", value="very"))
        llmobs_eval_metric_writer.periodic()
    mock_writer_logs.warning.assert_called_with(
        "A Datadog API key is required for sending data to LLM Observability in agentless mode. "
        "LLM Observability data will not be sent. Ensure an API key is set either via DD_API_KEY or via "
        "`LLMObs.enable(api_key=...)` before running your application."
    )


def test_send_categorical_metric(mock_writer_logs, llmobs_api_proxy_url):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
        interval=1,
        timeout=1,
        is_agentless=True,
        _api_key=DD_API_KEY,
        _override_url=llmobs_api_proxy_url,
    )
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event(label="toxicity", value="very"))
    llmobs_eval_metric_writer.periodic()
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("encoded %d LLMObs %s events to be sent", 1, "evaluation_metric")]
    )


def test_send_score_metric(mock_writer_logs, llmobs_api_proxy_url):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
        interval=1,
        timeout=1,
        is_agentless=True,
        _site=DD_SITE,
        _api_key=DD_API_KEY,
        _override_url=llmobs_api_proxy_url,
    )
    llmobs_eval_metric_writer.enqueue(_score_metric_event(label="sentiment", value=0.9))
    llmobs_eval_metric_writer.periodic()
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("encoded %d LLMObs %s events to be sent", 1, "evaluation_metric")]
    )


def test_send_timed_events(mock_writer_logs, llmobs_api_proxy_url):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
        interval=0.01,
        timeout=1,
        is_agentless=True,
        _api_key=DD_API_KEY,
        _override_url=llmobs_api_proxy_url,
    )
    llmobs_eval_metric_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_eval_metric_writer.enqueue(_score_metric_event(label="sentiment", value=0.9))
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("encoded %d LLMObs %s events to be sent", 1, "evaluation_metric")]
    )
    mock_writer_logs.reset_mock()
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event(label="toxicity", value="very"))
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("encoded %d LLMObs %s events to be sent", 1, "evaluation_metric")]
    )
    llmobs_eval_metric_writer.stop()


@pytest.mark.vcr_logs
def test_send_multiple_events(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(1, 1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    mock_writer_logs.reset_mock()
    llmobs_eval_metric_writer.enqueue(_score_metric_event(label="sentiment", value=0.9))
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event(label="toxicity", value="very"))
    llmobs_eval_metric_writer.periodic()
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("encoded %d LLMObs %s events to be sent", 2, "evaluation_metric")]
    )


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

    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(__file__)))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update({"PYTHONPATH": ":".join(pypath), "DD_LLMOBS_OVERRIDE_ORIGIN": mock_url})

    out, err, status, pid = run_python_code_in_subprocess(
        """
from ddtrace.llmobs._writer import LLMObsEvalMetricWriter
from tests.llmobs.test_llmobs_eval_metric_agentless_writer import _categorical_metric_event

llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
    interval=1000, timeout=1, is_agentless=True, _api_key="<not-a-real-key>"
)
llmobs_eval_metric_writer.start()
llmobs_eval_metric_writer.enqueue(_categorical_metric_event(label="toxicity", value="very"))
""",
        env=env,
    )

    server.shutdown()
    server.server_close()

    assert status == 0, err
    assert out == b""
    assert len(requests_received) == 1
