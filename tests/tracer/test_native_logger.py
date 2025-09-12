from contextlib import nullcontext

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
