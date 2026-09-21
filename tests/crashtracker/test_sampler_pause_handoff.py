import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_crashtracker_start_pauses_sampler_before_handler_swap(monkeypatch):
    """crashtracking.start must pause the sampler around the SIGSEGV handoff.

    Otherwise the sampling loop can observe the transient uninstall as a foreign
    takeover and permanently fall back to the syscall copy.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from unittest.mock import call

    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    order: list[str] = []
    stack_mod = SimpleNamespace(
        pause_sampling=MagicMock(side_effect=lambda: order.append("pause") or True),
        resume_sampling=MagicMock(side_effect=lambda: order.append("resume")),
        uninstall_segv_handler=MagicMock(side_effect=lambda: order.append("uninstall")),
        reinstall_segv_handler=MagicMock(side_effect=lambda: order.append("reinstall")),
    )
    monkeypatch.setitem(sys.modules, "ddtrace.internal.datadog.profiling.stack", stack_mod)
    monkeypatch.setattr(crashtracking, "is_available", True)
    monkeypatch.setattr(crashtracker_config, "enabled", True)
    monkeypatch.setattr(
        crashtracking,
        "_get_args",
        lambda *_a, **_k: (MagicMock(), MagicMock(), MagicMock()),
    )
    monkeypatch.setattr(crashtracking, "crashtracker_init", MagicMock())
    monkeypatch.setattr(crashtracking.excepthook, "register", MagicMock())
    monkeypatch.setattr(crashtracking.forksafe, "register", MagicMock())

    assert crashtracking.start()

    assert order == ["pause", "uninstall", "reinstall", "resume"]
    assert stack_mod.pause_sampling.mock_calls == [call()]
    assert stack_mod.resume_sampling.mock_calls == [call()]


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
def test_crashtracker_start_skips_uninstall_on_pause_timeout(monkeypatch):
    """When pause_sampling times out (None), do not uninstall or reinstall."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    stack_mod = SimpleNamespace(
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
    monkeypatch.setattr(crashtracking, "crashtracker_init", MagicMock())
    monkeypatch.setattr(crashtracking.excepthook, "register", MagicMock())
    monkeypatch.setattr(crashtracking.forksafe, "register", MagicMock())

    assert crashtracking.start()

    stack_mod.pause_sampling.assert_called_once()
    stack_mod.uninstall_segv_handler.assert_not_called()
    stack_mod.reinstall_segv_handler.assert_not_called()
    stack_mod.resume_sampling.assert_not_called()
