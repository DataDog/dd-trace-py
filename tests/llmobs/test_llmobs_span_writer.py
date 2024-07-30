import time

import mock
import pytest

from ddtrace import config
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _completion_event


INTAKE_URL = "https://llmobs-intake.%s" % config._dd_site
INTAKE_ENDPOINT = "%s/api/v2/llmobs" % INTAKE_URL


def test_writer_start(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1000, timeout=1)
    llmobs_span_writer.start()
    mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsSpanWriter", INTAKE_URL)])


def test_buffer_limit(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1000, timeout=1)
    for _ in range(1001):
        llmobs_span_writer.enqueue({})
    mock_writer_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "LLMObsSpanEncoder", 1000
    )


@pytest.mark.vcr_logs
def test_send_completion_event(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1, timeout=1)
    llmobs_span_writer.start()
    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])


@pytest.mark.vcr_logs
def test_send_chat_completion_event(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1, timeout=1)
    llmobs_span_writer.start()
    llmobs_span_writer.enqueue(_chat_completion_event())
    llmobs_span_writer.periodic()
    mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])


@pytest.mark.vcr_logs
def test_send_timed_events(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=0.01, timeout=1)
    llmobs_span_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_span_writer.enqueue(_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])
    mock_writer_logs.reset_mock()
    llmobs_span_writer.enqueue(_chat_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])


@pytest.mark.vcr_logs
def test_send_multiple_events(mock_writer_logs):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=0.01, timeout=1)
    llmobs_span_writer.start()
    mock_writer_logs.reset_mock()

    llmobs_span_writer.enqueue(_completion_event())
    llmobs_span_writer.enqueue(_chat_completion_event())
    time.sleep(0.1)
    mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 2)])


def test_send_on_exit(mock_writer_logs, run_python_code_in_subprocess):
    out, err, status, pid = run_python_code_in_subprocess(
        """
import atexit
import os
import time

from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs.test_llmobs_span_writer import _completion_event
from tests.llmobs._utils import logs_vcr

ctx = logs_vcr.use_cassette("tests.llmobs.test_llmobs_span_writer.test_send_completion_event.yaml")
ctx.__enter__()
atexit.register(lambda: ctx.__exit__())
llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=0.01, timeout=1)
llmobs_span_writer.start()
llmobs_span_writer.enqueue(_completion_event())
""",
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
