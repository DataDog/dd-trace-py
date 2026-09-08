"""Pin wrap() vs sys.monitoring for profiling asyncio hooks.

These fail if the version split in ddtrace.profiling._asyncio is reverted:
below 3.15 hooks must go through wrap() (in-place bytecode, not a bare
assignment); on 3.15+ task-creation must use the PY_RETURN monitoring path.
"""

from __future__ import annotations

import sys
from types import CodeType
from typing import Any
from typing import Callable

import pytest


@pytest.mark.skipif(sys.version_info >= (3, 15), reason="wrap() is the <3.15 path")
@pytest.mark.subprocess(err=None)
def test_asyncio_hooks_use_wrap_below_315() -> None:
    import asyncio
    import sys
    from types import FunctionType
    from types import ModuleType
    from typing import cast

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling import _asyncio

    assert stack.is_available, stack.failure_msg
    assert _asyncio.ASYNCIO_IMPORTED

    create_task: FunctionType = cast(FunctionType, asyncio.tasks.create_task)
    assert is_wrapped(create_task), "create_task must be wrap()'d, not monkey-patched"
    assert create_task.__name__ == "create_task"
    assert _asyncio._monitoring_tool_id is None
    assert _asyncio._py_return_handlers == {}

    tasks_mod: ModuleType = sys.modules["asyncio.tasks"]
    assert is_wrapped(cast(FunctionType, tasks_mod.as_completed))
    assert is_wrapped(cast(FunctionType, tasks_mod.shield))
    assert is_wrapped(cast(FunctionType, getattr(tasks_mod, "_wait")))
    gathering_future: type[object] = getattr(tasks_mod, "_GatheringFuture")
    assert is_wrapped(cast(FunctionType, gathering_future.__init__))

    events_module: ModuleType = sys.modules["asyncio.events"]
    policy_class: type[object] | None
    if sys.hexversion >= 0x030E0000:
        policy_class = getattr(events_module, "_BaseDefaultEventLoopPolicy", None)
    else:
        policy_class = getattr(events_module, "BaseDefaultEventLoopPolicy", None)
    assert policy_class is not None
    assert is_wrapped(cast(FunctionType, policy_class.set_event_loop))

    if sys.hexversion >= 0x030B0000:
        taskgroups: ModuleType | None = sys.modules.get("asyncio.taskgroups")
        assert taskgroups is not None
        assert is_wrapped(cast(FunctionType, taskgroups.TaskGroup.create_task))


@pytest.mark.skipif(sys.version_info < (3, 15), reason="sys.monitoring is the 3.15+ path")
@pytest.mark.subprocess(err=None)
def test_asyncio_task_creation_uses_monitoring_on_315() -> None:
    import asyncio
    import sys
    from types import FunctionType
    from types import ModuleType
    from typing import cast

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling import _asyncio

    assert stack.is_available, stack.failure_msg
    assert _asyncio.ASYNCIO_IMPORTED

    create_task: FunctionType = cast(FunctionType, asyncio.tasks.create_task)
    # A _register_return_hook that always returns False would skip this store
    # and fall back to wrap().
    assert create_task.__name__ == "create_task"
    assert not is_wrapped(create_task)
    assert _asyncio._monitoring_tool_id is not None
    assert id(create_task.__code__) in _asyncio._py_return_handlers

    # Non-task hooks continue using wrap(), including on 3.15+.
    tasks_mod: ModuleType = sys.modules["asyncio.tasks"]
    assert is_wrapped(cast(FunctionType, tasks_mod.as_completed))
    assert is_wrapped(cast(FunctionType, tasks_mod.shield))
    assert is_wrapped(cast(FunctionType, getattr(tasks_mod, "_wait")))
    gathering_future: type[object] = getattr(tasks_mod, "_GatheringFuture")
    assert is_wrapped(cast(FunctionType, gathering_future.__init__))

    taskgroups: ModuleType | None = sys.modules.get("asyncio.taskgroups")
    assert taskgroups is not None
    tg_create: FunctionType = cast(FunctionType, taskgroups.TaskGroup.create_task)
    assert tg_create.__name__ == "create_task"
    assert not is_wrapped(tg_create)
    assert id(tg_create.__code__) in _asyncio._py_return_handlers


@pytest.mark.skipif(sys.version_info < (3, 15), reason="sys.monitoring is the 3.15+ path")
def test_asyncio_return_hook_uses_ddtrace_monitoring_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from ddtrace.profiling import _asyncio

    def function() -> None:
        pass

    def callback(return_value: object) -> None:
        pass

    registered: list[tuple[CodeType, Any]] = []

    def register(code: CodeType, handler: Any) -> None:
        registered.append((code, handler))

    old_handler: Any = _asyncio._monitoring_handler
    old_tool_id: int | None = _asyncio._monitoring_tool_id
    old_handlers: dict[int, Callable[[object], None]] = _asyncio._py_return_handlers.copy()
    asyncio_monitoring: Any = getattr(_asyncio, "_monitoring")
    monkeypatch.setattr(asyncio_monitoring, "register", register)
    monkeypatch.setattr(asyncio_monitoring, "get_tool_id", lambda: 4)
    _asyncio._monitoring_handler = None
    _asyncio._monitoring_tool_id = None
    _asyncio._py_return_handlers.clear()

    try:
        assert _asyncio._register_return_hook(function, callback)
        assert registered == [(function.__code__, _asyncio._monitoring_handler)]
        assert _asyncio._monitoring_tool_id == 4
    finally:
        _asyncio._monitoring_handler = old_handler
        _asyncio._monitoring_tool_id = old_tool_id
        _asyncio._py_return_handlers.clear()
        _asyncio._py_return_handlers.update(old_handlers)
