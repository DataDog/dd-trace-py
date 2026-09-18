import time

import mock

from ddtrace.internal.evp_proxy.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.llmobs._constants import SPAN_ENDPOINT
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _completion_event
from tests.llmobs._utils import _large_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event
from tests.utils import override_global_config


INTAKE_ENDPOINT = agent_config.trace_agent_url
AGENT_PROXY_URL = "{}{}{}".format(INTAKE_ENDPOINT, EVP_PROXY_AGENT_BASE_PATH, SPAN_ENDPOINT)
UNIX_AGENT_INTAKE = "unix:///var/run/datadog/apm.sock"
UNIX_AGENT_PROXY_URL = "{}{}{}".format(UNIX_AGENT_INTAKE, EVP_PROXY_AGENT_BASE_PATH, SPAN_ENDPOINT)


def test_writer_start(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
    llmobs_span_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsSpanWriter", AGENT_PROXY_URL)])
    llmobs_span_writer.stop()


def test_unix_socket_writer_start(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False, _override_url=UNIX_AGENT_INTAKE)
    llmobs_span_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsSpanWriter", UNIX_AGENT_PROXY_URL)])
    llmobs_span_writer.stop()


def test_buffer_limit(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
    for _ in range(1001):
        llmobs_span_writer.enqueue({})
    mock_writer_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "LLMObsSpanWriter", 1000
    )


@mock.patch("ddtrace.llmobs._writer.LLMObsSpanWriter._send_payload")
def test_flush_queue_when_event_cause_queue_to_exceed_payload_limit(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
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


@mock.patch("ddtrace.llmobs._writer.LLMObsSpanWriter._send_payload")
def test_truncating_oversized_events(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
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


@mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload")
def test_send_completion_event(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


@mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload")
def test_send_chat_completion_event(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
    llmobs_span_writer.enqueue(_chat_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


@mock.patch("ddtrace.internal.utils.retry.sleep")
@mock.patch("ddtrace.llmobs._writer.telemetry.record_dropped_payload")
@mock.patch("ddtrace.llmobs._writer.get_connection")
def test_connection_error_logs_debug_before_retry_and_no_error_after_success(
    mock_get_connection, mock_record_dropped_payload, mock_sleep, mock_writer_logs
):
    connection_error = ConnectionError("temporary connection failure")
    failed_connection = mock.Mock()
    failed_connection.request.side_effect = connection_error
    successful_connection = mock.Mock()
    successful_connection.getresponse.return_value.status = 200
    successful_connection.getresponse.return_value.read.return_value = b"OK"
    mock_get_connection.side_effect = [failed_connection, successful_connection]

    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()

    retry_log = mock.call(
        "attempt to send %d LLMObs %s events to %s failed, will retry if attempts remain: %r",
        1,
        "span",
        llmobs_span_writer._intake,
        connection_error,
        extra={"send_to_telemetry": False},
    )
    assert mock_writer_logs.debug.call_args_list.count(retry_log) == 1
    assert mock_get_connection.call_count == 2
    mock_writer_logs.error.assert_not_called()
    mock_record_dropped_payload.assert_not_called()


@mock.patch("ddtrace.internal.utils.retry.sleep")
@mock.patch("ddtrace.llmobs._writer.telemetry.record_dropped_payload")
@mock.patch("ddtrace.llmobs._writer.get_connection")
def test_connection_error_logs_one_error_after_retries_exhausted(
    mock_get_connection, mock_record_dropped_payload, mock_sleep, mock_writer_logs
):
    connection_error = ConnectionError("persistent connection failure")
    mock_get_connection.return_value.request.side_effect = connection_error

    llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()

    retry_log = mock.call(
        "attempt to send %d LLMObs %s events to %s failed, will retry if attempts remain: %r",
        1,
        "span",
        llmobs_span_writer._intake,
        connection_error,
        extra={"send_to_telemetry": False},
    )
    assert mock_writer_logs.debug.call_args_list.count(retry_log) == llmobs_span_writer.RETRY_ATTEMPTS
    mock_writer_logs.error.assert_called_once_with(
        "failed to send %d LLMObs %s events to %s",
        1,
        "span",
        llmobs_span_writer._intake,
        exc_info=True,
        extra={"send_to_telemetry": False},
    )
    mock_record_dropped_payload.assert_called_once_with(1, event_type="span", error="connection_error")


@mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload")
def test_send_timed_events(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(0.01, 1, is_agentless=False)
    llmobs_span_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_span_writer.enqueue(_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])
    mock_writer_logs.reset_mock()
    llmobs_span_writer.enqueue(_chat_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


@mock.patch("ddtrace.llmobs._writer.LLMObsSpanWriter._send_payload")
def test_configurable_payload_size_limit(mock_send_payload, mock_writer_logs):
    """DD_LLMOBS_PAYLOAD_SIZE_BYTES overrides the flush threshold."""
    with override_global_config(dict(_llmobs_payload_size_limit=100)):
        llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
        llmobs_span_writer.enqueue(_completion_event())
        llmobs_span_writer.enqueue(_completion_event())
    mock_writer_logs.debug.assert_any_call(
        "manually flushing buffer because queueing next event will exceed EVP payload limit"
    )


def test_configurable_event_size_limit(mock_writer_logs):
    """DD_LLMOBS_EVENT_SIZE_BYTES overrides the truncation threshold."""
    with override_global_config(dict(_llmobs_event_size_limit=100)):
        llmobs_span_writer = LLMObsSpanWriter(1, 1, is_agentless=False)
        llmobs_span_writer.enqueue(_completion_event())
    mock_writer_logs.warning.assert_called_once_with(
        "dropping event input/output because its size (%d) exceeds the event size limit (%d bytes)",
        mock.ANY,
        100,
    )
