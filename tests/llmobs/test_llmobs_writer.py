import os
import time

import mock
import pytest

from ddtrace.llmobs._writer import LLMObsWriter


INTAKE_ENDPOINT = "https://llmobs-intake.datad0g.com/api/v2/llmobs"
DD_SITE = "datad0g.com"
dd_api_key = os.getenv("DD_API_KEY", default="<not-a-real-api-key>")


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs._writer.logger") as m:
        yield m


def _completion_event():
    return {
        "kind": "llm",
        "span_id": "12345678901",
        "trace_id": "98765432101",
        "parent_id": "",
        "session_id": "98765432101",
        "name": "completion_span",
        "tags": ["version:", "env:", "service:", "source:integration"],
        "start_ns": 1707763310981223236,
        "duration": 12345678900,
        "error": 0,
        "meta": {
            "span.kind": "llm",
            "model_name": "ada",
            "model_provider": "openai",
            "input": {
                "messages": [{"content": "who broke enigma?"}],
                "parameters": {"temperature": 0, "max_tokens": 256},
            },
            "output": {
                "messages": [
                    {
                        "content": "\n\nThe Enigma code was broken by a team of codebreakers at Bletchley Park, led by mathematician Alan Turing."  # noqa: E501
                    }
                ]
            },
        },
        "metrics": {"prompt_tokens": 64, "completion_tokens": 128, "total_tokens": 192},
    }


def _chat_completion_event():
    return {
        "span_id": "12345678902",
        "trace_id": "98765432102",
        "parent_id": "",
        "session_id": "98765432102",
        "name": "chat_completion_span",
        "tags": ["version:", "env:", "service:", "source:integration"],
        "start_ns": 1707763310981223936,
        "duration": 12345678900,
        "error": 0,
        "meta": {
            "span.kind": "llm",
            "model_name": "gpt-3.5-turbo",
            "model_provider": "openai",
            "input": {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an evil dark lord looking for his one ring to rule them all",
                    },
                    {"role": "user", "content": "I am a hobbit looking to go to Mordor"},
                ],
                "parameters": {"temperature": 0.9, "max_tokens": 256},
            },
            "output": {
                "messages": [
                    {
                        "content": "Ah, a bold and foolish hobbit seeking to challenge my dominion in Mordor. Very well, little creature, I shall play along. But know that I am always watching, and your quest will not go unnoticed",  # noqa: E501
                        "role": "assistant",
                    },
                ]
            },
        },
        "metrics": {"prompt_tokens": 64, "completion_tokens": 128, "total_tokens": 192},
    }


def test_buffer_limit(mock_logs):
    llmobs_writer = LLMObsWriter(site="datadoghq.com", api_key="asdf", interval=1000, timeout=1)
    for _ in range(1001):
        llmobs_writer.enqueue({})
    mock_logs.warning.assert_called_with("LLMobs event buffer full (limit is %d), dropping record", 1000)


@pytest.mark.vcr_logs
def test_send_completion_event(mock_logs):
    llmobs_writer = LLMObsWriter(site="datad0g.com", api_key=dd_api_key, interval=1, timeout=1)
    llmobs_writer.start()
    mock_logs.debug.assert_has_calls([mock.call("started llmobs writer to %r", INTAKE_ENDPOINT)])
    llmobs_writer.enqueue(_completion_event())
    mock_logs.reset_mock()
    llmobs_writer.periodic()
    mock_logs.debug.assert_has_calls([mock.call("sent %d LLMObs events to %r", 1, INTAKE_ENDPOINT)])


@pytest.mark.vcr_logs
def test_send_chat_completion_event(mock_logs):
    llmobs_writer = LLMObsWriter(site="datad0g.com", api_key=dd_api_key, interval=1, timeout=1)
    llmobs_writer.start()
    mock_logs.debug.assert_has_calls([mock.call("started llmobs writer to %r", INTAKE_ENDPOINT)])
    llmobs_writer.enqueue(_chat_completion_event())
    mock_logs.reset_mock()
    llmobs_writer.periodic()
    mock_logs.debug.assert_has_calls([mock.call("sent %d LLMObs events to %r", 1, INTAKE_ENDPOINT)])


@pytest.mark.vcr_logs
def test_send_completion_bad_api_key(mock_logs):
    llmobs_writer = LLMObsWriter(site="datad0g.com", api_key="<bad-api-key>", interval=1, timeout=1)
    llmobs_writer.start()
    llmobs_writer.enqueue(_completion_event())
    llmobs_writer.periodic()
    mock_logs.error.assert_called_with(
        "failed to send %d LLMObs events to %r, got response code %r, status: %r",
        1,
        INTAKE_ENDPOINT,
        403,
        b'{"errors":[{"status":"403","title":"Forbidden","detail":"API key is invalid"}]}',
    )


@pytest.mark.vcr_logs
def test_send_timed_events(mock_logs):
    llmobs_writer = LLMObsWriter(site="datad0g.com", api_key=dd_api_key, interval=0.01, timeout=1)
    llmobs_writer.start()
    mock_logs.reset_mock()

    llmobs_writer.enqueue(_completion_event())
    time.sleep(0.1)
    mock_logs.debug.assert_has_calls([mock.call("sent %d LLMObs events to %r", 1, INTAKE_ENDPOINT)])
    mock_logs.reset_mock()
    llmobs_writer.enqueue(_chat_completion_event())
    time.sleep(0.1)
    mock_logs.debug.assert_has_calls([mock.call("sent %d LLMObs events to %r", 1, INTAKE_ENDPOINT)])


@pytest.mark.vcr_logs
def test_send_multiple_events(mock_logs):
    llmobs_writer = LLMObsWriter(site="datad0g.com", api_key=dd_api_key, interval=0.01, timeout=1)
    llmobs_writer.start()
    mock_logs.reset_mock()

    llmobs_writer.enqueue(_completion_event())
    llmobs_writer.enqueue(_chat_completion_event())
    time.sleep(0.1)
    mock_logs.debug.assert_has_calls([mock.call("sent %d LLMObs events to %r", 2, INTAKE_ENDPOINT)])


def test_send_on_exit(mock_logs, run_python_code_in_subprocess):
    out, err, status, pid = run_python_code_in_subprocess(
        """
import atexit
import os
import time

from ddtrace.llmobs._writer import LLMObsWriter
from tests.llmobs.test_llmobs_writer import _completion_event
from tests.llmobs.utils import logs_vcr

ctx = logs_vcr.use_cassette("tests.llmobs.test_llmobs_writer.test_send_on_exit.yaml")
ctx.__enter__()
atexit.register(lambda: ctx.__exit__())
llmobs_writer = LLMObsWriter(site="datad0g.com", api_key=os.getenv("DD_API_KEY"), interval=0.01, timeout=1)
llmobs_writer.start()
llmobs_writer.enqueue(_completion_event())
""",
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
