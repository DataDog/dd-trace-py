from contextlib import nullcontext
import os
import uuid

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
cases = [(config, msg, LEVELS.index(msg) >= LEVELS.index(config)) for config in LEVELS for msg in LEVELS]


@pytest.mark.parametrize("backend", ["stdout", "stderr", "file"])
@pytest.mark.parametrize("configured_level, message_level, should_log", cases)
def test_logger_subprocess(
    backend, configured_level, message_level, should_log, tmp_path, ddtrace_run_python_code_in_subprocess
):
    log_path = tmp_path / f"{backend}_{configured_level}_{message_level}.log"

    env = os.environ.copy()
    env["_DD_TRACE_WRITER_NATIVE"] = "1"
    env["_DD_NATIVE_LOGGING_BACKEND"] = backend
    env["_DD_NATIVE_LOGGING_FILE_PATH"] = log_path
    env["_DD_NATIVE_LOGGING_LOG_LEVEL"] = configured_level

    message = f"msg_{uuid.uuid4().hex}"
    code = """
from ddtrace.internal.native._native import logger

message_level = f"{}"
logger.log(message_level, f"{}")
    """.format(
        message_level, message
    )
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)

    assert status == 0
    if backend == "stdout":
        found = message in out.decode("utf8")
        assert err == b""
        assert found == should_log
    elif backend == "stderr":
        found = message in err.decode("utf8")
        assert out == b""
        assert found == should_log
    else:  # file
        found = message in log_path.read_text()
        assert out == b""
        assert err == b""
        assert found == should_log
