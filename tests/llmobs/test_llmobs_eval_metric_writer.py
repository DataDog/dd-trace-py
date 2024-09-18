import os
import time

import mock
import pytest

from ddtrace.llmobs._writer import LLMObsEvalMetricWriter


INTAKE_ENDPOINT = "https://api.datad0g.com/api/intake/llm-obs/v1/eval-metric"
DD_SITE = "datad0g.com"
dd_api_key = os.getenv("DD_API_KEY", default="<not-a-real-api-key>")


def _categorical_metric_event():
    return {
        "span_id": "12345678901",
        "trace_id": "98765432101",
        "metric_type": "categorical",
        "categorical_value": "very",
        "label": "toxicity",
        "ml_app": "dummy-ml-app",
        "timestamp_ms": round(time.time() * 1000),
    }


def _score_metric_event():
    return {
        "span_id": "12345678902",
        "trace_id": "98765432102",
        "metric_type": "score",
        "label": "sentiment",
        "score_value": 0.9,
        "ml_app": "dummy-ml-app",
        "timestamp_ms": round(time.time() * 1000),
    }


def test_writer_start(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(site="datad0g.com", api_key=dd_api_key, interval=1000, timeout=1)
    llmobs_eval_metric_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsEvalMetricWriter", INTAKE_ENDPOINT)])


def test_buffer_limit(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(site="datadoghq.com", api_key="asdf", interval=1000, timeout=1)
    for _ in range(1001):
        llmobs_eval_metric_writer.enqueue({})
    mock_writer_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "LLMObsEvalMetricWriter", 1000
    )


@pytest.mark.vcr_logs
def test_send_metric_bad_api_key(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
        site="datad0g.com", api_key="<bad-api-key>", interval=1000, timeout=1
    )
    llmobs_eval_metric_writer.start()
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event())
    llmobs_eval_metric_writer.periodic()
    mock_writer_logs.error.assert_called_with(
        "failed to send %d LLMObs %s events to %s, got response code %d, status: %s",
        1,
        "evaluation_metric",
        INTAKE_ENDPOINT,
        403,
        b'{"status":"error","code":403,"errors":["Forbidden"],"statuspage":"http://status.datadoghq.com","twitter":"http://twitter.com/datadogops","email":"support@datadoghq.com"}',  # noqa
    )


@pytest.mark.vcr_logs
def test_send_categorical_metric(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(site="datad0g.com", api_key=dd_api_key, interval=1000, timeout=1)
    llmobs_eval_metric_writer.start()
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event())
    llmobs_eval_metric_writer.periodic()
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("sent %d LLMObs %s events to %s", 1, "evaluation_metric", INTAKE_ENDPOINT)]
    )


@pytest.mark.vcr_logs
def test_send_score_metric(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(site="datad0g.com", api_key=dd_api_key, interval=1000, timeout=1)
    llmobs_eval_metric_writer.start()
    llmobs_eval_metric_writer.enqueue(_score_metric_event())
    llmobs_eval_metric_writer.periodic()
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("sent %d LLMObs %s events to %s", 1, "evaluation_metric", INTAKE_ENDPOINT)]
    )


@pytest.mark.vcr_logs
def test_send_timed_events(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(site="datad0g.com", api_key=dd_api_key, interval=0.01, timeout=1)
    llmobs_eval_metric_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_eval_metric_writer.enqueue(_score_metric_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("sent %d LLMObs %s events to %s", 1, "evaluation_metric", INTAKE_ENDPOINT)]
    )
    mock_writer_logs.reset_mock()
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("sent %d LLMObs %s events to %s", 1, "evaluation_metric", INTAKE_ENDPOINT)]
    )


@pytest.mark.vcr_logs
def test_send_multiple_events(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(site="datad0g.com", api_key=dd_api_key, interval=0.01, timeout=1)
    llmobs_eval_metric_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_eval_metric_writer.enqueue(_score_metric_event())
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls(
        [mock.call("sent %d LLMObs %s events to %s", 2, "evaluation_metric", INTAKE_ENDPOINT)]
    )


def test_send_on_exit(mock_writer_logs, run_python_code_in_subprocess):
    out, err, status, pid = run_python_code_in_subprocess(
        """
import atexit
import os
import time

from ddtrace.llmobs._writer import LLMObsEvalMetricWriter
from tests.llmobs.test_llmobs_eval_metric_writer import _score_metric_event
from tests.llmobs._utils import logs_vcr

ctx = logs_vcr.use_cassette("tests.llmobs.test_llmobs_eval_metric_writer.send_score_metric.yaml")
ctx.__enter__()
atexit.register(lambda: ctx.__exit__())
llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
site="datad0g.com", api_key=os.getenv("DD_API_KEY"), interval=0.01, timeout=1
)
llmobs_eval_metric_writer.start()
llmobs_eval_metric_writer.enqueue(_score_metric_event())
""",
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
