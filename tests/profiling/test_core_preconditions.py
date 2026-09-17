from typing import Any
from unittest import mock

import pytest

from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.profiling import _core_preconditions


@pytest.mark.parametrize(
    "limit,expected",
    [
        (0, "zero"),
        (1024, "limited"),
    ],
)
def test_categorize_rlimit(limit: int, expected: str) -> None:
    assert _core_preconditions._categorize_rlimit(limit) == expected


def test_categorize_rlimit_unlimited() -> None:
    import resource

    assert _core_preconditions._categorize_rlimit(resource.RLIM_INFINITY) == "unlimited"


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ("|/usr/lib/systemd/systemd-coredump %P %u %g %s %t %c %h", "pipe_systemd"),
        ("|/usr/local/bin/custom-coredump %P", "pipe_custom"),
        ("/tmp/core.%e.%p", "file"),
        ("core", "other"),
        ("/dev/null", "disabled"),
        ("", "disabled"),
    ],
)
def test_read_core_pattern_shape(pattern: str, expected: str) -> None:
    mock_open = mock.mock_open(read_data=pattern)
    with mock.patch("builtins.open", mock_open):
        assert _core_preconditions._read_core_pattern_shape() == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "no"),
        (1, "yes"),
        (2, "suid"),
        (99, "unknown"),
    ],
)
def test_read_dumpable(value: int, expected: str) -> None:
    with mock.patch("ddtrace.profiling._core_preconditions._prctl_get_dumpable", return_value=value):
        assert _core_preconditions._read_dumpable() == expected


def test_read_dumpable_unknown_on_oserror() -> None:
    with mock.patch(
        "ddtrace.profiling._core_preconditions._prctl_get_dumpable",
        side_effect=OSError(1, "prctl(PR_GET_DUMPABLE) failed"),
    ):
        assert _core_preconditions._read_dumpable() == "unknown"


def test_emit_core_preconditions_telemetry_linux() -> None:
    core_pattern: str = "|/usr/lib/systemd/systemd-coredump %P"

    def fake_open(path: str, *args: Any, **kwargs: Any) -> Any:
        if path == "/proc/sys/kernel/core_pattern":
            return mock.mock_open(read_data=core_pattern)()
        raise OSError(path)

    with (
        mock.patch("ddtrace.profiling._core_preconditions.sys.platform", "linux"),
        mock.patch("resource.getrlimit", return_value=(0, 1024)),
        mock.patch("ddtrace.profiling._core_preconditions._prctl_get_dumpable", return_value=0),
        mock.patch("builtins.open", side_effect=fake_open),
        mock.patch("ddtrace.profiling._core_preconditions.telemetry_writer.add_log") as mock_add_log,
    ):
        _core_preconditions.emit_core_preconditions_telemetry()

    mock_add_log.assert_called_once()
    call_args = mock_add_log.call_args
    assert call_args[0][0] == TELEMETRY_LOG_LEVEL.DEBUG
    assert call_args[0][1] == "Profiler startup core dump preconditions"
    assert call_args[1]["tags"] == {
        "rlimit_core_soft": "zero",
        "rlimit_core_hard": "limited",
        "core_pattern": "pipe_systemd",
        "dumpable": "no",
    }


def test_emit_core_preconditions_telemetry_skips_non_linux() -> None:
    with (
        mock.patch("ddtrace.profiling._core_preconditions.sys.platform", "darwin"),
        mock.patch("ddtrace.profiling._core_preconditions.telemetry_writer.add_log") as mock_add_log,
    ):
        _core_preconditions.emit_core_preconditions_telemetry()

    mock_add_log.assert_not_called()
