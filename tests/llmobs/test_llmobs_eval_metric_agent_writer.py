import time

import mock

from ddtrace.llmobs._constants import EVAL_ENDPOINT
from ddtrace.llmobs._constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.llmobs._writer import LLMObsEvalMetricWriter
from ddtrace.settings._agent import config as agent_config
from tests.llmobs.test_llmobs_eval_metric_agentless_writer import _categorical_metric_event
from tests.llmobs.test_llmobs_eval_metric_agentless_writer import _score_metric_event


INTAKE_ENDPOINT = agent_config.trace_agent_url
AGENT_PROXY_URL = f"{INTAKE_ENDPOINT}{EVP_PROXY_AGENT_BASE_PATH}{EVAL_ENDPOINT}"


def test_writer_start(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(1, 1, is_agentless=False)
    llmobs_eval_metric_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsEvalMetricWriter", AGENT_PROXY_URL)])
    llmobs_eval_metric_writer.stop()


def test_buffer_limit(mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(1, 1, is_agentless=False)
    for _ in range(1001):
        llmobs_eval_metric_writer.enqueue({})
    mock_writer_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "LLMObsEvalMetricWriter", 1000
    )


@mock.patch("ddtrace.llmobs._writer.LLMObsEvalMetricWriter._send_payload")
def test_send_categorical_metrics(mock_send_payload, mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(1, 1, is_agentless=False)
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event())
    llmobs_eval_metric_writer.periodic()
    mock_writer_logs.debug.assert_called_with("encoded %d LLMObs %s events to be sent", 1, "evaluation_metric")


@mock.patch("ddtrace.llmobs._writer.LLMObsEvalMetricWriter._send_payload")
def test_send_score_metric(mock_send_payload, mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(1, 1, is_agentless=False)
    llmobs_eval_metric_writer.enqueue(_score_metric_event())
    llmobs_eval_metric_writer.periodic()
    mock_writer_logs.debug.assert_called_with("encoded %d LLMObs %s events to be sent", 1, "evaluation_metric")


@mock.patch("ddtrace.llmobs._writer.LLMObsEvalMetricWriter._send_payload")
def test_send_timed_events(mock_send_payload, mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(0.01, 1, is_agentless=False)
    llmobs_eval_metric_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_eval_metric_writer.enqueue(_score_metric_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_called_with("encoded %d LLMObs %s events to be sent", 1, "evaluation_metric")
    mock_writer_logs.reset_mock()
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_called_with("encoded %d LLMObs %s events to be sent", 1, "evaluation_metric")
    llmobs_eval_metric_writer.stop()


@mock.patch("ddtrace.llmobs._writer.LLMObsEvalMetricWriter._send_payload")
def test_send_multiple_events(mock_send_payload, mock_writer_logs):
    llmobs_eval_metric_writer = LLMObsEvalMetricWriter(1, 1, is_agentless=False)
    mock_writer_logs.reset_mock()
    llmobs_eval_metric_writer.enqueue(_score_metric_event())
    llmobs_eval_metric_writer.enqueue(_categorical_metric_event())
    llmobs_eval_metric_writer.periodic()
    mock_writer_logs.debug.assert_called_with("encoded %d LLMObs %s events to be sent", 2, "evaluation_metric")
