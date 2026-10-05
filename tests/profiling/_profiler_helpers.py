"""Shared helpers for profiler-under-test construction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing import TypeAlias

import pytest

from ddtrace.profiling import profiler


_ModuleHook: TypeAlias = tuple[str, Callable[[Any], None]]


class TestProfiler(profiler._ProfilerInstance):
    """``_ProfilerInstance`` that skips default exporter construction.

    Collector start/stop and import-hook tests do not need a live exporter.
    """

    __test__: bool = False

    def _build_default_exporters(self, *args: Any, **kargs: Any) -> None:
        return None


def install_recording_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[_ModuleHook], list[_ModuleHook]]:
    """Replace ``profiler.ModuleWatchdog`` with a class that records hook calls."""
    registered_hooks: list[_ModuleHook] = []
    unregistered_hooks: list[_ModuleHook] = []

    class WatchdogMock:
        @staticmethod
        def register_module_hook(module: str, hook: Callable[[Any], None]) -> None:
            registered_hooks.append((module, hook))

        @staticmethod
        def unregister_module_hook(module: str, hook: Callable[[Any], None]) -> None:
            unregistered_hooks.append((module, hook))

    monkeypatch.setattr(profiler, "ModuleWatchdog", WatchdogMock)
    return registered_hooks, unregistered_hooks
