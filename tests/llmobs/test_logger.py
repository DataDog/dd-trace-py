import gzip
import json
import time
from unittest import mock

import pytest

from ddtrace.llmobs._log_writer import V2LogWriter


_URL = "https://http-intake.logs.datadoghq.com/api/v2/logs"


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.internal.logs.logger") as m:
        yield m


@pytest.fixture
def mock_connection():
    """Stand in for the intake connection; tests set the response status and body."""
    with mock.patch("ddtrace.internal.logs.get_connection") as get_connection:
        connection = get_connection.return_value
        connection.getresponse.return_value.status = 202
        yield connection


def _sent_events(connection):
    return [json.loads(gzip.decompress(call.args[2])) for call in connection.request.call_args_list]


def _test_log():
    return {
        "timestamp": time.time() * 1000,
        "message": "test msg",
        "hostname": "dev-mbp",
        "ddsource": "python",
        "service": "",
        "status": "info",
        "ddtags": "",
    }


def test_buffer_limit(mock_logs):
    logger = V2LogWriter(site="datadoghq.com", api_key="asdf", interval=1000, timeout=1)
    for _ in range(1001):
        logger.enqueue({})
    mock_logs.warning.assert_called_with("log buffer full (limit is %d), dropping log", 1000)
    assert len(logger._buffer) == 1000


def test_send_log(mock_logs, mock_connection):
    logger = V2LogWriter(site="datadoghq.com", api_key="test-key", interval=1, timeout=1)
    logger.start()
    mock_logs.debug.assert_has_calls([mock.call("started log writer to %r", _URL)])
    log = _test_log()
    logger.enqueue(log)
    mock_logs.reset_mock()
    logger.periodic()
    mock_logs.debug.assert_has_calls([mock.call("sent %d logs to %r", 1, _URL)])

    method, path, _, headers = mock_connection.request.call_args.args
    assert (method, path) == ("POST", "/api/v2/logs")
    assert headers["DD-API-KEY"] == "test-key"
    assert _sent_events(mock_connection) == [[log]]
    logger.stop()


def test_send_log_bad_api_key(mock_logs, mock_connection):
    body = b'{"errors":[{"status":"403","title":"Forbidden","detail":"API key is invalid"}]}'
    mock_connection.getresponse.return_value.status = 403
    mock_connection.getresponse.return_value.read.return_value = body
    logger = V2LogWriter(site="datadoghq.com", api_key="asdf", interval=1, timeout=1)
    logger.start()
    logger.enqueue(_test_log())
    logger.periodic()
    mock_logs.error.assert_called_with(
        "failed to send %d logs to %r, got response code %r, status: %r",
        1,
        _URL,
        403,
        body,
        extra={"send_to_telemetry": False},
    )
    logger.stop()


def test_send_timed(mock_logs, mock_connection):
    logger = V2LogWriter(site="datadoghq.com", api_key="test-key", interval=0.01, timeout=1)
    logger.start()

    logger.enqueue(_test_log())
    logger.enqueue(_test_log())
    mock_logs.reset_mock()

    time.sleep(0.1)
    mock_logs.debug.assert_has_calls([mock.call("sent %d logs to %r", 2, _URL)])

    logger.enqueue(_test_log())
    logger.enqueue(_test_log())
    mock_logs.reset_mock()

    time.sleep(0.1)
    mock_logs.debug.assert_has_calls([mock.call("sent %d logs to %r", 2, _URL)])
    logger.stop()


@pytest.mark.subprocess(out="sent 1 log(s)\n")
def test_send_on_exit():
    import atexit
    import gzip
    import json
    import time
    from unittest import mock

    from ddtrace.llmobs._log_writer import V2LogWriter

    get_connection = mock.patch("ddtrace.internal.logs.get_connection").start()
    get_connection.return_value.getresponse.return_value.status = 202

    def report():
        (call,) = get_connection.return_value.request.call_args_list
        print("sent %d log(s)" % len(json.loads(gzip.decompress(call.args[2]))))

    # Handlers run in stack order, so this one runs after the writer's flush registered by start().
    atexit.register(report)
    logger = V2LogWriter(site="datadoghq.com", api_key="test-key", interval=1000, timeout=1)
    logger.start()
    logger.enqueue(
        {
            "timestamp": time.time() * 1000,
            "message": "test msg",
            "hostname": "dev-mbp",
            "ddsource": "python",
            "service": "",
            "status": "info",
            "ddtags": "",
        }
    )
