import os
import time

import mock
import pytest

from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _completion_event
from tests.llmobs._utils import _large_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event


DD_SITE = "datad0g.com"
DD_API_KEY = os.getenv("STG_API_KEY", default="<not-a-real-api-key>")
INTAKE_BASE_URL = "https://llmobs-intake.%s" % DD_SITE
INTAKE_URL = "%s/api/v2/llmobs" % INTAKE_BASE_URL


def test_writer_start(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(
        interval=1000, timeout=1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY
    )
    llmobs_span_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsSpanWriter", INTAKE_URL)])
    llmobs_span_writer.stop()


def test_buffer_limit(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(
        interval=1000, timeout=1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY
    )
    for _ in range(1001):
        llmobs_span_writer.enqueue({})
    mock_writer_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "LLMObsSpanWriter", 1000
    )


@mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload")
def test_flush_queue_when_event_cause_queue_to_exceed_payload_limit(mock_send_payload, mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
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


def test_truncating_oversized_events(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
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


@pytest.mark.vcr_logs
def test_send_completion_event(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


@pytest.mark.vcr_logs
def test_send_chat_completion_event(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    llmobs_span_writer.enqueue(_chat_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 1, "span")])


@pytest.mark.vcr_logs
def test_send_completion_bad_api_key(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(
        interval=1, timeout=1, is_agentless=True, _site=DD_SITE, _api_key="<bad-api-key>"
    )
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.error.assert_called_with(
        "failed to send %d LLMObs %s events to %s, got response code %d, status: %s",
        1,
        "span",
        "https://llmobs-intake.datad0g.com/api/v2/llmobs",
        403,
        b'{"errors":[{"status":"403","title":"Forbidden","detail":"API key is invalid"}]}',
    )


@pytest.mark.vcr_logs
def test_send_timed_events(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(
        interval=0.01, timeout=1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY
    )
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
    llmobs_span_writer = LLMObsSpanWriter(interval=1, timeout=1, is_agentless=True, _site=DD_SITE, _api_key=DD_API_KEY)
    mock_writer_logs.reset_mock()

    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.enqueue(_chat_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encoded %d LLMObs %s events to be sent", 2, "span")])


def test_send_on_exit(run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(__file__)))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update({"PYTHONPATH": ":".join(pypath)})

    out, err, status, pid = run_python_code_in_subprocess(
        """
import atexit

from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs.test_llmobs_span_agentless_writer import _completion_event
from tests.llmobs._utils import logs_vcr

ctx = logs_vcr.use_cassette("tests.llmobs.test_llmobs_span_agentless_writer.test_send_completion_event.yaml")
ctx.__enter__()
atexit.register(lambda: ctx.__exit__())
llmobs_span_writer = LLMObsSpanWriter(
    interval=0.01, timeout=1, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>",
)
llmobs_span_writer.start()
llmobs_span_writer.enqueue(_completion_event())
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
