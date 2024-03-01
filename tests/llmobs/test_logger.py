import os
import time

import mock
import pytest

from ddtrace.llmobs._log_writer import V2LogWriter


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs._log_writer.logger") as m:
        yield m


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


@pytest.mark.vcr_logs
def test_send_log(mock_logs):
    logger = V2LogWriter(site="datadoghq.com", api_key=os.getenv("DD_API_KEY"), interval=1, timeout=1)
    logger.start()
    mock_logs.debug.assert_has_calls(
        [mock.call("started log writer to %r", "https://http-intake.logs.datadoghq.com/api/v2/logs")]
    )
    logger.enqueue(_test_log())
    mock_logs.reset_mock()
    logger.periodic()
    mock_logs.debug.assert_has_calls(
        [mock.call("sent %d logs to %r", 1, "https://http-intake.logs.datadoghq.com/api/v2/logs")]
    )


@pytest.mark.vcr_logs
def test_send_log_bad_api_key(mock_logs):
    logger = V2LogWriter(site="datadoghq.com", api_key="asdf", interval=1, timeout=1)
    logger.start()
    logger.enqueue(_test_log())
    logger.periodic()
    mock_logs.error.assert_called_with(
        "failed to send %d logs to %r, got response code %r, status: %r",
        1,
        "https://http-intake.logs.datadoghq.com/api/v2/logs",
        403,
        b'{"errors":[{"status":"403","title":"Forbidden","detail":"API key is invalid"}]}',
    )


@pytest.mark.vcr_logs
def test_send_timed(mock_logs):
    logger = V2LogWriter(site="datadoghq.com", api_key=os.getenv("DD_API_KEY"), interval=0.01, timeout=1)
    logger.start()

    logger.enqueue(_test_log())
    logger.enqueue(_test_log())
    mock_logs.reset_mock()

    time.sleep(0.1)
    mock_logs.debug.assert_has_calls(
        [mock.call("sent %d logs to %r", 2, "https://http-intake.logs.datadoghq.com/api/v2/logs")]
    )

    logger.enqueue(_test_log())
    logger.enqueue(_test_log())
    mock_logs.reset_mock()

    time.sleep(0.1)
    mock_logs.debug.assert_has_calls(
        [mock.call("sent %d logs to %r", 2, "https://http-intake.logs.datadoghq.com/api/v2/logs")]
    )


@pytest.mark.subprocess()
def test_send_on_exit():
    import atexit
    import os
    import time

    from ddtrace.llmobs._log_writer import V2LogWriter
    from tests.llmobs._utils import logs_vcr

    ctx = logs_vcr.use_cassette("tests.llmobs.test_logger.test_send_on_exit.yaml")
    ctx.__enter__()
    # Save the cassette at exit, this atexit handler has to be
    # registered before logger.start() is called so that the request
    # can be captured. Handlers run in stack order.
    atexit.register(lambda: ctx.__exit__())
    logger = V2LogWriter(site="datadoghq.com", api_key=os.getenv("DD_API_KEY"), interval=1, timeout=1)
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
