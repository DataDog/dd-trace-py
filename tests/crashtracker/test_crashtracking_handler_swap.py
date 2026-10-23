"""Unit tests for crashtracking ↔ stack profiler handler coordination."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_start_reinstalls_stack_handler_when_init_raises() -> None:
    """If crashtracker_init raises after uninstall, finally must reinstall before resume."""
    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    if not stack.is_available:
        pytest.skip("stack profiler native extension unavailable")

    call_order: list[str] = []
    mock_reinstall: MagicMock = MagicMock(side_effect=lambda: call_order.append("reinstall"))
    mock_resume: MagicMock = MagicMock(side_effect=lambda: call_order.append("resume"))

    with (
        patch.object(crashtracking, "is_available", True),
        patch.object(crashtracker_config, "enabled", True),
        patch.object(crashtracking, "_get_args", return_value=(object(), object(), object())),
        patch.object(crashtracking, "crashtracker_init", side_effect=RuntimeError("boom")),
        patch.object(stack, "pause_sampling", return_value=True),
        patch.object(stack, "uninstall_segv_handler"),
        patch.object(stack, "reinstall_segv_handler", mock_reinstall),
        patch.object(stack, "resume_sampling", mock_resume),
    ):
        started: bool = crashtracking.start()

    assert started is False
    mock_reinstall.assert_called()
    mock_resume.assert_called_once()
    assert call_order.index("reinstall") < call_order.index("resume"), call_order


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_start_reinstalls_stack_handler_on_success_before_resume() -> None:
    """Success path must still reinstall (in finally) before resuming a paused sampler."""
    from ddtrace.internal import excepthook
    from ddtrace.internal import forksafe
    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    if not stack.is_available:
        pytest.skip("stack profiler native extension unavailable")

    call_order: list[str] = []
    mock_reinstall: MagicMock = MagicMock(side_effect=lambda: call_order.append("reinstall"))
    mock_resume: MagicMock = MagicMock(side_effect=lambda: call_order.append("resume"))

    with (
        patch.object(crashtracking, "is_available", True),
        patch.object(crashtracker_config, "enabled", True),
        patch.object(crashtracking, "_get_args", return_value=(object(), object(), object())),
        patch.object(crashtracking, "crashtracker_init"),
        patch.object(excepthook, "register"),
        patch.object(forksafe, "register"),
        patch.object(stack, "pause_sampling", return_value=True),
        patch.object(stack, "uninstall_segv_handler"),
        patch.object(stack, "reinstall_segv_handler", mock_reinstall),
        patch.object(stack, "resume_sampling", mock_resume),
    ):
        started: bool = crashtracking.start()

    assert started is True
    mock_reinstall.assert_called()
    mock_resume.assert_called_once()
    assert call_order.index("reinstall") < call_order.index("resume"), call_order
