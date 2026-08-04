"""Origin-task linking must not run unless the stack sampler has enabled it."""

from __future__ import annotations

import asyncio
import sys
import types
from unittest import mock

from ddtrace.contrib.internal.futures import threading as futures_threading


def test_available_but_inactive_stack_skips_asyncio_current_task(monkeypatch) -> None:
    # settings.profiling can leave the stack module imported (is_available=True)
    # while the sampler has never been started. Submit must not call current_task.
    fake_stack = types.ModuleType(futures_threading._PROFILING_STACK_MODULE)
    fake_stack.is_available = True
    fake_stack.is_origin_task_linking_enabled = lambda: False
    monkeypatch.setitem(sys.modules, futures_threading._PROFILING_STACK_MODULE, fake_stack)

    with mock.patch.object(asyncio, "current_task") as current_task:
        assert futures_threading._current_origin_task() == (None, None)
        current_task.assert_not_called()


def test_enabled_stack_queries_current_task(monkeypatch) -> None:
    fake_stack = types.ModuleType(futures_threading._PROFILING_STACK_MODULE)
    fake_stack.is_available = True
    fake_stack.is_origin_task_linking_enabled = lambda: True
    monkeypatch.setitem(sys.modules, futures_threading._PROFILING_STACK_MODULE, fake_stack)

    fake_task = mock.Mock()
    fake_task.get_name.return_value = "origin"
    with mock.patch.object(asyncio, "current_task", return_value=fake_task) as current_task:
        task_id, task_name = futures_threading._current_origin_task()
        current_task.assert_called_once_with()
        assert task_id == id(fake_task)
        assert task_name == "origin"
