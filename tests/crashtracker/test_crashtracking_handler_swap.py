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


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_handler_swap_lock_blocks_nested_faulthandler_during_crashtracking_start() -> None:
    """crashtracking.start must hold handler_swap_lock across pause/swap/resume.

    Sampler::pause is not nested: a concurrent faulthandler.enable that resumes
    first would clear crashtracking's pause while handlers are still swapped.
    """
    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack._handler_swap_lock import handler_swap_lock
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    if not stack.is_available:
        pytest.skip("stack profiler native extension unavailable")

    saw_lock_held: list[bool] = []

    def _pause_while_holding_shared_lock() -> bool:
        held: bool = not handler_swap_lock.acquire(blocking=False)
        saw_lock_held.append(held)
        if not held:
            handler_swap_lock.release()
        return True

    with (
        patch.object(crashtracking, "is_available", True),
        patch.object(crashtracker_config, "enabled", True),
        patch.object(crashtracking, "_get_args", return_value=(object(), object(), object())),
        patch.object(crashtracking, "crashtracker_init"),
        patch("ddtrace.internal.excepthook.register"),
        patch("ddtrace.internal.forksafe.register"),
        patch.object(stack, "pause_sampling", side_effect=_pause_while_holding_shared_lock),
        patch.object(stack, "uninstall_segv_handler"),
        patch.object(stack, "reinstall_segv_handler"),
        patch.object(stack, "resume_sampling"),
    ):
        started: bool = crashtracking.start()

    assert started is True
    assert saw_lock_held == [True], saw_lock_held


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_start_reinstalls_when_keyboardinterrupt_during_uninstall() -> None:
    """KeyboardInterrupt after uninstall must still reinstall and resume.

    uninstall sits inside the try/finally with init: BaseException skips
    except Exception, so leaving uninstall outside finally would leave sampling
    paused without recovery handlers when the app catches the interrupt.
    """
    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    if not stack.is_available:
        pytest.skip("stack profiler native extension unavailable")

    call_order: list[str] = []

    def _uninstall_then_interrupt() -> None:
        call_order.append("uninstall")
        raise KeyboardInterrupt()

    mock_reinstall: MagicMock = MagicMock(side_effect=lambda: call_order.append("reinstall"))
    mock_resume: MagicMock = MagicMock(side_effect=lambda: call_order.append("resume"))

    with (
        patch.object(crashtracking, "is_available", True),
        patch.object(crashtracker_config, "enabled", True),
        patch.object(crashtracking, "_get_args", return_value=(object(), object(), object())),
        patch.object(crashtracking, "crashtracker_init"),
        patch.object(stack, "pause_sampling", return_value=True),
        patch.object(stack, "uninstall_segv_handler", side_effect=_uninstall_then_interrupt),
        patch.object(stack, "reinstall_segv_handler", mock_reinstall),
        patch.object(stack, "resume_sampling", mock_resume),
    ):
        with pytest.raises(KeyboardInterrupt):
            crashtracking.start()

    mock_reinstall.assert_called()
    mock_resume.assert_called_once()
    assert call_order.index("uninstall") < call_order.index("reinstall") < call_order.index("resume"), call_order


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_start_resumes_when_keyboardinterrupt_during_pause() -> None:
    """KeyboardInterrupt during pause_sampling must still cancel the pause.

    pause sits outside the uninstall try/finally: BaseException skips
    ``except Exception``, and if the interrupt lands after the native pause
    armed but before ``pause_result`` is assigned, resume must not depend on
    seeing ``True``.
    """
    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    if not stack.is_available:
        pytest.skip("stack profiler native extension unavailable")

    mock_resume: MagicMock = MagicMock()
    mock_reinstall: MagicMock = MagicMock()
    mock_uninstall: MagicMock = MagicMock()

    with (
        patch.object(crashtracking, "is_available", True),
        patch.object(crashtracker_config, "enabled", True),
        patch.object(crashtracking, "_get_args", return_value=(object(), object(), object())),
        patch.object(crashtracking, "crashtracker_init"),
        patch.object(stack, "pause_sampling", side_effect=KeyboardInterrupt()),
        patch.object(stack, "uninstall_segv_handler", mock_uninstall),
        patch.object(stack, "reinstall_segv_handler", mock_reinstall),
        patch.object(stack, "resume_sampling", mock_resume),
    ):
        with pytest.raises(KeyboardInterrupt):
            crashtracking.start()

    mock_resume.assert_called_once()
    mock_uninstall.assert_not_called()
    mock_reinstall.assert_not_called()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_set_fast_copy_holds_handler_swap_lock() -> None:
    """set_fast_copy must hold handler_swap_lock while reclaiming handlers.

    Otherwise a concurrent faulthandler.enable can uninstall, then set_fast_copy
    reinstalls, then enable saves us as its previous and reinstall builds a cycle.
    """
    import threading
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack._handler_swap_lock import handler_swap_lock

    if not stack.is_available:
        pytest.skip("stack profiler native extension unavailable")

    started: threading.Event = threading.Event()
    finished: threading.Event = threading.Event()
    error: list[BaseException] = []

    def _call_set_fast_copy() -> None:
        started.set()
        try:
            stack.set_fast_copy(True)
        except BaseException as exc:  # noqa: BLE001 — capture for main thread
            error.append(exc)
        finally:
            finished.set()

    handler_swap_lock.acquire()
    try:
        thread: threading.Thread = threading.Thread(target=_call_set_fast_copy)
        thread.start()
        assert started.wait(timeout=2.0), "set_fast_copy thread did not start"
        # Still blocked on the lock: must not finish while we hold it.
        time.sleep(0.2)
        assert not finished.is_set(), "set_fast_copy returned while handler_swap_lock was held"
    finally:
        handler_swap_lock.release()

    assert finished.wait(timeout=2.0), "set_fast_copy did not finish after lock release"
    thread.join(timeout=2.0)
    assert error == [], error
