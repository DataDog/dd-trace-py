from __future__ import annotations

import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_crashtracker_start_pauses_sampler_before_handler_swap(monkeypatch: pytest.MonkeyPatch) -> None:
    """crashtracking.start must pause the sampler around the SIGSEGV handoff.

    Otherwise the sampling loop can observe the transient uninstall as a foreign
    takeover and permanently fall back to the syscall copy.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from unittest.mock import call

    from ddtrace.internal import excepthook
    from ddtrace.internal import forksafe
    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    order: list[str] = []

    def _record(name: str, result: bool | None = None) -> bool | None:
        order.append(name)
        return result

    stack_mod: SimpleNamespace = SimpleNamespace(
        pause_sampling=MagicMock(side_effect=lambda: _record("pause", True)),
        resume_sampling=MagicMock(side_effect=lambda: _record("resume")),
        uninstall_segv_handler=MagicMock(side_effect=lambda: _record("uninstall")),
        reinstall_segv_handler=MagicMock(side_effect=lambda: _record("reinstall")),
    )
    monkeypatch.setitem(sys.modules, "ddtrace.internal.datadog.profiling.stack", stack_mod)
    monkeypatch.setattr(crashtracking, "is_available", True)
    monkeypatch.setattr(crashtracker_config, "enabled", True)
    monkeypatch.setattr(
        crashtracking,
        "_get_args",
        lambda *_a, **_k: (MagicMock(), MagicMock(), MagicMock()),
    )
    monkeypatch.setattr(crashtracking, "crashtracker_init", MagicMock(), raising=False)
    monkeypatch.setattr(excepthook, "register", MagicMock())
    monkeypatch.setattr(forksafe, "register", MagicMock())

    started: bool = crashtracking.start()
    assert started

    assert order == ["pause", "uninstall", "reinstall", "resume"]
    assert stack_mod.pause_sampling.mock_calls == [call()]
    assert stack_mod.resume_sampling.mock_calls == [call()]


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_crashtracker_start_skips_uninstall_on_pause_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """When pause_sampling times out (None), do not uninstall the profiler handler."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from ddtrace.internal import excepthook
    from ddtrace.internal import forksafe
    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    stack_mod: SimpleNamespace = SimpleNamespace(
        pause_sampling=MagicMock(return_value=None),
        resume_sampling=MagicMock(),
        uninstall_segv_handler=MagicMock(),
        reinstall_segv_handler=MagicMock(),
    )
    monkeypatch.setitem(sys.modules, "ddtrace.internal.datadog.profiling.stack", stack_mod)
    monkeypatch.setattr(crashtracking, "is_available", True)
    monkeypatch.setattr(crashtracker_config, "enabled", True)
    monkeypatch.setattr(
        crashtracking,
        "_get_args",
        lambda *_a, **_k: (MagicMock(), MagicMock(), MagicMock()),
    )
    monkeypatch.setattr(crashtracking, "crashtracker_init", MagicMock(), raising=False)
    monkeypatch.setattr(excepthook, "register", MagicMock())
    monkeypatch.setattr(forksafe, "register", MagicMock())

    started: bool = crashtracking.start()
    assert started

    stack_mod.pause_sampling.assert_called_once()
    stack_mod.uninstall_segv_handler.assert_not_called()
    stack_mod.reinstall_segv_handler.assert_called_once()
    stack_mod.resume_sampling.assert_not_called()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_crashtracker_fork_handoff_reinstalls(monkeypatch: pytest.MonkeyPatch) -> None:
    """crashtracker_on_fork must use the same pause/uninstall/reinstall sequence."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from ddtrace.internal import excepthook
    from ddtrace.internal import forksafe
    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    order: list[str] = []

    def _record(name: str, result: bool | None = None) -> bool | None:
        order.append(name)
        return result

    stack_mod: SimpleNamespace = SimpleNamespace(
        pause_sampling=MagicMock(side_effect=lambda: _record("pause", False)),
        resume_sampling=MagicMock(side_effect=lambda: _record("resume")),
        uninstall_segv_handler=MagicMock(side_effect=lambda: _record("uninstall")),
        reinstall_segv_handler=MagicMock(side_effect=lambda: _record("reinstall")),
    )
    fork_handler: list[object] = []
    on_fork_mock: MagicMock = MagicMock()
    monkeypatch.setitem(sys.modules, "ddtrace.internal.datadog.profiling.stack", stack_mod)
    monkeypatch.setattr(crashtracking, "is_available", True)
    monkeypatch.setattr(crashtracker_config, "enabled", True)
    monkeypatch.setattr(
        crashtracking,
        "_get_args",
        lambda *_a, **_k: (MagicMock(), MagicMock(), MagicMock()),
    )
    monkeypatch.setattr(crashtracking, "crashtracker_init", MagicMock(), raising=False)
    monkeypatch.setattr(crashtracking, "crashtracker_on_fork", on_fork_mock, raising=False)
    monkeypatch.setattr(excepthook, "register", MagicMock())
    monkeypatch.setattr(forksafe, "register", lambda fn: fork_handler.append(fn))

    started: bool = crashtracking.start()
    assert started
    assert fork_handler

    order.clear()
    handler: object = fork_handler[0]
    assert callable(handler)
    handler()
    assert order == ["pause", "uninstall", "reinstall"]
    on_fork_mock.assert_called_once()
