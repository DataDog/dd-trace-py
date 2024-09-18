import os
import time

import mock

from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _completion_event
from tests.llmobs._utils import _large_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event
from tests.utils import override_global_config


DATADOG_SITE = "datad0g.com"
INTAKE_URL = "https://llmobs-intake.%s" % DATADOG_SITE
INTAKE_ENDPOINT = "%s/api/v2/llmobs" % INTAKE_URL


def test_writer_start(mock_writer_logs):
    with override_global_config(dict(_dd_api_key="foobar.baz", _dd_site=DATADOG_SITE)):
        llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1000, timeout=1)
        llmobs_span_writer.start()
        mock_writer_logs.debug.assert_has_calls([mock.call("started %r to %r", "LLMObsSpanWriter", INTAKE_URL)])


def test_buffer_limit(mock_writer_logs, mock_http_writer_send_payload_response):
    with override_global_config(dict(_dd_api_key="foobar.baz", _dd_site=DATADOG_SITE)):
        llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1000, timeout=1)
        for _ in range(1001):
            llmobs_span_writer.enqueue({})
        mock_writer_logs.warning.assert_called_with(
            "%r event buffer full (limit is %d), dropping event", "LLMObsSpanEncoder", 1000
        )


def test_flush_queue_when_event_cause_queue_to_exceed_payload_limit(
    mock_writer_logs, mock_http_writer_send_payload_response
):
    with override_global_config(dict(_dd_api_key="foobar.baz", _dd_site=DATADOG_SITE)):
        llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1000, timeout=1)
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
            ]
        )


def test_truncating_oversized_events(mock_writer_logs, mock_http_writer_send_payload_response):
    with override_global_config(dict(_dd_api_key="foobar.baz", _dd_site=DATADOG_SITE)):
        llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1000, timeout=1)
        llmobs_span_writer.enqueue(_oversized_llm_event())
        llmobs_span_writer.enqueue(_oversized_retrieval_event())
        llmobs_span_writer.enqueue(_oversized_workflow_event())
        mock_writer_logs.warning.assert_has_calls(
            [
                mock.call(
                    "dropping event input/output because its size (%d) exceeds the event size limit (1MB)", 1400708
                ),
                mock.call(
                    "dropping event input/output because its size (%d) exceeds the event size limit (1MB)", 1400448
                ),
                mock.call(
                    "dropping event input/output because its size (%d) exceeds the event size limit (1MB)", 1400429
                ),
            ]
        )


def test_send_completion_event(mock_writer_logs, mock_http_writer_logs, mock_http_writer_send_payload_response):
    with override_global_config(
        dict(
            _dd_site=DATADOG_SITE,
            _dd_api_key="foobar.baz",
        )
    ):
        llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1, timeout=1)
        llmobs_span_writer.start()
        llmobs_span_writer.enqueue(_completion_event())
        llmobs_span_writer.periodic()
        mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])
        mock_http_writer_logs.error.assert_not_called()


def test_send_chat_completion_event(mock_writer_logs, mock_http_writer_logs, mock_http_writer_send_payload_response):
    with override_global_config(
        dict(
            _dd_site=DATADOG_SITE,
            _dd_api_key="foobar.baz",
        )
    ):
        llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1, timeout=1)
        llmobs_span_writer.start()
        llmobs_span_writer.enqueue(_chat_completion_event())
        llmobs_span_writer.periodic()
        mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 1)])
        mock_http_writer_logs.error.assert_not_called()


def test_send_completion_bad_api_key(mock_http_writer_logs, mock_http_writer_put_response_forbidden):
    with override_global_config(dict(_dd_site=DATADOG_SITE, _dd_api_key="<bad-api-key>")):
        llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=1, timeout=1)
        llmobs_span_writer.start()
        llmobs_span_writer.enqueue(_completion_event())
        llmobs_span_writer.periodic()
        mock_http_writer_logs.error.assert_called_with(
            "failed to send traces to intake at %s: HTTP error status %s, reason %s",
            INTAKE_ENDPOINT,
            403,
            b'{"errors":[{"status":"403","title":"Forbidden","detail":"API key is invalid"}]}',
        )


def test_send_timed_events(mock_writer_logs, mock_http_writer_logs, mock_http_writer_send_payload_response):
    with override_global_config(
        dict(
            _dd_site=DATADOG_SITE,
            _dd_api_key="foobar.baz",
        )
    ):
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
        mock_http_writer_logs.error.assert_not_called()


def test_send_multiple_events(mock_writer_logs, mock_http_writer_logs, mock_http_writer_send_payload_response):
    with override_global_config(
        dict(
            _dd_site=DATADOG_SITE,
            _dd_api_key="foobar.baz",
        )
    ):
        llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=0.01, timeout=1)
        llmobs_span_writer.start()
        mock_writer_logs.reset_mock()

        llmobs_span_writer.enqueue(_completion_event())
        llmobs_span_writer.enqueue(_chat_completion_event())
        time.sleep(0.1)
        mock_writer_logs.debug.assert_has_calls([mock.call("encode %d LLMObs span events to be sent", 2)])
        mock_http_writer_logs.error.assert_not_called()


def test_send_on_exit(mock_writer_logs, run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "DD_API_KEY": "foobar.baz",
            "DD_SITE": DATADOG_SITE,
            "PYTHONPATH": ":".join(pypath),
        }
    )

    out, err, status, pid = run_python_code_in_subprocess(
        """
import mock
import os
import time

from ddtrace.internal.utils.http import Response
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.llmobs.test_llmobs_span_agentless_writer import _completion_event

with mock.patch(
    "ddtrace.internal.writer.HTTPWriter._send_payload",
    return_value=Response(
        status=200,
        body="{}",
    ),
):
    llmobs_span_writer = LLMObsSpanWriter(is_agentless=True, interval=0.01, timeout=1)
    llmobs_span_writer.start()
    llmobs_span_writer.enqueue(_completion_event())
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
