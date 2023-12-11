import os
import time

import mock
import pytest
import vcr

from ddtrace.internal.llmobs import LLMObsWriter
from tests.utils import request_token


INTAKE_ENDPOINT = "https://api.datad0g.com/api/unstable/llm-obs/v1/records"


logs_vcr = vcr.VCR(
    cassette_library_dir=os.path.join(os.path.dirname(__file__), "llmobs_cassettes/"),
    record_mode="once",
    match_on=["path"],
    filter_headers=[("DD-API-KEY", "XXXXXX"), ("DD-APPLICATION-KEY", "XXXXXX")],
    # Ignore requests to the agent
    ignore_localhost=True,
)


@pytest.fixture(autouse=True)
def vcr_logs(request):
    marks = [m for m in request.node.iter_markers(name="vcr_logs")]
    assert len(marks) < 2
    if marks:
        mark = marks[0]
        cass = mark.kwargs.get("cassette", request_token(request).replace(" ", "_").replace(os.path.sep, "_"))
        with logs_vcr.use_cassette("%s.yaml" % cass):
            yield
    else:
        yield


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.internal.llmobs.writer.logger") as m:
        yield m


def _completion_record():
    return {
        "type": "completion",
        "id": "cmpl-76n1xLvRKv3mfjx7hJ41UHrHy9ar6",
        "timestamp": 1681852797000,
        "model": "ada",
        "model_provider": "openai",
        "input": {
            "prompts": ["who broke enigma?"],
            "temperature": 0,
            "max_tokens": 256,
        },
        "output": {
            "completions": [
                {
                    "content": "\n\nThe Enigma code was broken by a team of codebreakers at Bletchley Park, "
                    "led by mathematician Alan Turing."
                }
            ],
            "durations": [1.234],
        },
    }


def _chat_completion_record():
    return {
        "type": "chat",
        "id": "chatcmpl-76n5heroUX66dt3wGtwp0tFFedLLu",
        "timestamp": 1681853029000,
        "model": "gpt-3.5-turbo",
        "model_provider": "openai",
        "input": {
            "messages": [
                {"role": "system", "content": "You are an evil dark lord looking for his one ring to rule them all"},
                {"role": "user", "content": "I am a hobbit looking to go to Mordor"},
            ],
            "temperature": 0.9,
            "max_tokens": 256,
        },
        "output": {
            "completions": [
                {
                    "content": "Ah, a bold and foolish hobbit seeking to challenge my dominion in Mordor. Very well, "
                    "little creature, I shall play along. But know that I am always watching, "
                    "and your quest will not go unnoticed",
                    "role": "assistant",
                }
            ],
            "durations": [2.345],
        },
    }


def test_buffer_limit(mock_logs):
    llmobs_writer = LLMObsWriter(site="datadoghq.com", api_key="asdf", app_key="asdf", interval=1000, timeout=1)
    for _ in range(1001):
        llmobs_writer.enqueue({})
    mock_logs.warning.assert_called_with("LLMobs record buffer full (limit is %d), dropping record", 1000)


@pytest.mark.vcr_logs
def test_send_completion(mock_logs):
    llmobs_writer = LLMObsWriter(
        site="datad0g.com", api_key=os.getenv("DD_API_KEY"), app_key=os.getenv("DD_APP_KEY"), interval=1, timeout=1
    )
    llmobs_writer.start()
    mock_logs.debug.assert_has_calls([mock.call("started llmobs writer to %r", INTAKE_ENDPOINT)])
    llmobs_writer.enqueue(_completion_record())
    mock_logs.reset_mock()
    llmobs_writer.periodic()
    mock_logs.debug.assert_has_calls([mock.call("sent %d LLM records to %r", 1, INTAKE_ENDPOINT)])


@pytest.mark.vcr_logs
def test_send_chat_completion(mock_logs):
    llmobs_writer = LLMObsWriter(
        site="datad0g.com", api_key=os.getenv("DD_API_KEY"), app_key=os.getenv("DD_APP_KEY"), interval=1, timeout=1
    )
    llmobs_writer.start()
    mock_logs.debug.assert_has_calls([mock.call("started llmobs writer to %r", INTAKE_ENDPOINT)])
    llmobs_writer.enqueue(_chat_completion_record())
    mock_logs.reset_mock()
    llmobs_writer.periodic()
    mock_logs.debug.assert_has_calls([mock.call("sent %d LLM records to %r", 1, INTAKE_ENDPOINT)])


@pytest.mark.vcr_logs
def test_send_completion_bad_api_key(mock_logs):
    llmobs_writer = LLMObsWriter(
        site="datad0g.com", api_key="<not-a-real-api-key>", app_key=os.getenv("DD_APP_KEY"), interval=1, timeout=1
    )
    llmobs_writer.start()
    llmobs_writer.enqueue(_completion_record())
    llmobs_writer.periodic()
    mock_logs.error.assert_called_with(
        "failed to send %d LLM records to %r, got response code %r, status: %r",
        1,
        INTAKE_ENDPOINT,
        403,
        b'{"status":"error","code":403,"errors":["Forbidden"],"statuspage":"http://status.datadoghq.com",'
        b'"twitter":"http://twitter.com/datadogops","email":"support@datadoghq.com"}',
    )


@pytest.mark.vcr_logs
def test_send_completion_bad_app_key(mock_logs):
    llmobs_writer = LLMObsWriter(
        site="datad0g.com", api_key=os.getenv("DD_API_KEY"), app_key="<not-a-real-app-key>", interval=1, timeout=1
    )
    llmobs_writer.start()
    llmobs_writer.enqueue(_completion_record())
    llmobs_writer.periodic()
    mock_logs.error.assert_called_with(
        "failed to send %d LLM records to %r, got response code %r, status: %r",
        1,
        INTAKE_ENDPOINT,
        403,
        b'{"status":"error","code":403,"errors":["Forbidden"],"statuspage":"http://status.datadoghq.com",'
        b'"twitter":"http://twitter.com/datadogops","email":"support@datadoghq.com"}',
    )


@pytest.mark.vcr_logs
def test_send_timed_records(mock_logs):
    llmobs_writer = LLMObsWriter(
        site="datad0g.com", api_key=os.getenv("DD_API_KEY"), app_key=os.getenv("DD_APP_KEY"), interval=0.01, timeout=1
    )
    llmobs_writer.start()
    mock_logs.reset_mock()

    llmobs_writer.enqueue(_completion_record())
    llmobs_writer.enqueue(_completion_record())
    time.sleep(0.1)
    mock_logs.debug.assert_has_calls([mock.call("sent %d LLM records to %r", 2, INTAKE_ENDPOINT)])

    llmobs_writer.enqueue(_chat_completion_record())
    mock_logs.reset_mock()
    time.sleep(0.1)
    mock_logs.debug.assert_has_calls([mock.call("sent %d LLM records to %r", 1, INTAKE_ENDPOINT)])


def test_send_on_exit(mock_logs, run_python_code_in_subprocess):
    out, err, status, pid = run_python_code_in_subprocess(
        """
import atexit
import os
import time

from ddtrace.internal.llmobs import LLMObsWriter
from tests.internal.test_llmobs import _completion_record
from tests.internal.test_llmobs import logs_vcr

ctx = logs_vcr.use_cassette("tests.internal.test_llmobs.test_send_on_exit.yaml")
ctx.__enter__()
atexit.register(lambda: ctx.__exit__())
llmobs_writer = LLMObsWriter(
    site="datad0g.com", api_key=os.getenv("DD_API_KEY"), app_key=os.getenv("DD_APP_KEY"), interval=0.01, timeout=1
)
llmobs_writer.start()
llmobs_writer.enqueue(_completion_record())
""",
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
