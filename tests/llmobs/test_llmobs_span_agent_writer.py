import time

import mock

from ddtrace.internal import agent
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _completion_event


INTAKE_ENDPOINT = agent.get_trace_url()


def test_writer_start(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=False, interval=1000, timeout=1)
    llmobs_span_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsSpanWriter", INTAKE_ENDPOINT)])


def test_buffer_limit(mock_writer_logs, mock_http_writer_send_payload_response):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=False, interval=1000, timeout=1)
    for _ in range(1001):
        llmobs_span_writer.enqueue({})
    mock_writer_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "LLMObsSpanEncoder", 1000
    )


def test_send_completion_event(mock_writer_logs, mock_http_writer_send_payload_response):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=False, interval=1000, timeout=1)
    llmobs_span_writer.start()
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])


def test_send_chat_completion_event(mock_writer_logs, mock_http_writer_send_payload_response):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=False, interval=1000, timeout=1)
    llmobs_span_writer.start()
    llmobs_span_writer.enqueue(_chat_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])


def test_send_timed_events(mock_writer_logs, mock_http_writer_send_payload_response):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=False, interval=0.05, timeout=1)
    llmobs_span_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_span_writer.enqueue(_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])
    mock_writer_logs.reset_mock()
    llmobs_span_writer.enqueue(_chat_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])
