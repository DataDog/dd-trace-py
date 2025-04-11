import time

import mock

from ddtrace.llmobs._constants import EVP_PROXY_SPAN_ENDPOINT
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.settings._agent import config as agent_config
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _completion_event
from tests.llmobs._utils import _large_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event


INTAKE_ENDPOINT = agent_config.trace_agent_url
AGENT_PROXY_URL = "{}{}".format(INTAKE_ENDPOINT, EVP_PROXY_SPAN_ENDPOINT)


def test_writer_start(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1000, timeout=1, is_agentless=False)
    llmobs_span_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsSpanWriter", AGENT_PROXY_URL)])


def test_buffer_limit(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1000, timeout=1, is_agentless=False)
    for _ in range(1001):
        llmobs_span_writer.enqueue({})
    mock_writer_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "LLMObsSpanWriter", 1000
    )


@mock.patch("ddtrace.llmobs._writer.LLMObsSpanWriter._send_payload")
def test_flush_queue_when_event_cause_queue_to_exceed_payload_limit(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=False)
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls(
        [
            mock.call("manually flushing buffer because queueing next event will exceed EVP payload limit"),
            mock.call("encoded %d LLMObs %s events to be sent", 5, "span"),
            mock.call("encoded %d LLMObs %s events to be sent", 1, "span"),
        ],
        any_order=True,
    )


@mock.patch("ddtrace.llmobs._writer.LLMObsSpanWriter._send_payload")
def test_truncating_oversized_events(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=False)
    llmobs_span_writer.enqueue(_oversized_llm_event())
    llmobs_span_writer.enqueue(_oversized_retrieval_event())
    llmobs_span_writer.enqueue(_oversized_workflow_event())
    mock_writer_logs.warning.assert_has_calls(
        [
            mock.call("dropping event input/output because its size (%d) exceeds the event size limit (1MB)", 1400724),
            mock.call("dropping event input/output because its size (%d) exceeds the event size limit (1MB)", 1400464),
            mock.call("dropping event input/output because its size (%d) exceeds the event size limit (1MB)", 1400445),
        ]
    )


@mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload")
def test_send_completion_event(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=False)
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


@mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload")
def test_send_chat_completion_event(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=False)
    llmobs_span_writer.enqueue(_chat_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


@mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload")
def test_send_timed_events(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=0.01, timeout=1, is_agentless=False)
    llmobs_span_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_span_writer.enqueue(_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])
    mock_writer_logs.reset_mock()
    llmobs_span_writer.enqueue(_chat_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])
