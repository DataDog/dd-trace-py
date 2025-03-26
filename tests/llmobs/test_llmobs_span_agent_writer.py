import time

import mock

from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.settings._agent import config as agent_config
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _completion_event
from tests.llmobs._utils import _large_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event


INTAKE_ENDPOINT = agent_config.trace_agent_url


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


def test_flush_queue_when_event_cause_queue_to_exceed_payload_limit(
    mock_writer_logs, mock_http_writer_send_payload_response
):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=False, interval=1000, timeout=1)
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    llmobs_span_writer.enqueue(_large_event())
    mock_writer_logs.debug.assert_has_calls(
        [
            mock.call("flushing queue because queuing next event will exceed EVP payload limit"),
            mock.call("encode %d LLMObs span events to be sent", 5),
        ],
        any_order=True,
    )


def test_truncating_oversized_events(mock_writer_logs, mock_http_writer_send_payload_response):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=False, interval=1000, timeout=1)
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
