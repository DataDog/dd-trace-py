from contextlib import nullcontext
import os
import time

import pytest

from ddtrace.internal.native._native import logger


@pytest.mark.parametrize(
    "output, expected",
    [
        ("stdout", nullcontext()),
        ("stderr", nullcontext()),
        ("file", nullcontext()),
    ],
)
def test_logger_disable(output, expected):
    logger.configure()
    with expected:
        logger.disable(output)


@pytest.mark.parametrize(
    "log_level, expected",
    [
        ("trace", nullcontext()),
        ("debug", nullcontext()),
        ("info", nullcontext()),
        ("warn", nullcontext()),
        ("error", nullcontext()),
        ("invalid", pytest.raises(ValueError)),
    ],
)
def test_logger_set_log_level(log_level, expected):
    logger.configure()
    with expected as ex:
        logger.set_log_level(log_level)
    if log_level == "invalid":
        assert "Invalid log level" in str(ex.value)


@pytest.mark.parametrize("output", [None, "stdout", "stderr", "file"])
@pytest.mark.parametrize("path", [None, "/tmp/log.txt"])
@pytest.mark.parametrize("files", [None, 2])
@pytest.mark.parametrize("max_bytes", [None, 4096])
def test_logger_configure(output, path, files, max_bytes):
    if output is None:
        kwargs = {}
    else:
        kwargs = {"output": output}
        if path is not None:
            kwargs["path"] = path
        if files is not None:
            kwargs["max_files"] = files
        if max_bytes is not None:
            kwargs["max_size_bytes"] = bytes

    if output == "file":
        if path is None:
            with pytest.raises(ValueError):
                logger.configure(**kwargs)
    else:
        logger.configure(**kwargs)


LEVELS = ["trace", "debug", "info", "warn", "error"]


def wait_for_log(path, expected, timeout=1):
    start = time.time()
    while time.time() - start < timeout:
        if expected in path.read_text():
            return True
        time.sleep(0.1)
    return False


cases = [(config, msg, LEVELS.index(msg) >= LEVELS.index(config)) for config in LEVELS for msg in LEVELS]


@pytest.mark.parametrize("configured_level, message_level, should_log", cases)
def test_logger_levels(configured_level, message_level, should_log, tmp_path):
    log_path = tmp_path / "log.txt"
    logger.configure(output="file", path=str(log_path))
    logger.set_log_level(configured_level)

    message = f"{message_level}_msg"
    logger.log(message_level, message)

    found = wait_for_log(log_path, message)
    assert found == should_log


@pytest.mark.parametrize("backend", ["stdout", "stderr", "file"])
def test_logger_subprocess(backend, tmp_path, ddtrace_run_python_code_in_subprocess):
    log_path = tmp_path / "log.txt"

    env = os.environ.copy()
    env["_DD_TRACE_WRITER_NATIVE"] = "1"
    env["_DD_NATIVE_LOGGING_BACKEND"] = backend
    env["_DD_NATIVE_LOGGING_FILE_PATH"] = log_path

    code = """
from ddtrace.internal.native._native import logger

logger.log("warn", "message")
    """
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)

    assert status == 0
    if backend == "stdout":
        assert "message" in out.decode("utf-8")
        assert err == b""
    elif backend == "stderr":
        assert "message" in err.decode("utf-8")
        assert out == b""
    else:
        found = wait_for_log(log_path, "message")
        assert out == b""
        assert err == b""
        assert found
