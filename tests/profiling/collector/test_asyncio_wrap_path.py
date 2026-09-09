"""Pin wrap() vs sys.monitoring for profiling asyncio hooks.

These fail if the version split in ddtrace.profiling._asyncio is reverted:
below 3.15 hooks must go through wrap() (in-place bytecode, not a bare
assignment); on 3.15+ task-creation must use the PY_RETURN monitoring path.

The registration helper and the PY_RETURN dispatch are deliberately kept
outside that version gate, so the tests at the bottom of this file exercise
them on every supported interpreter with a stubbed monitoring module.
"""

from __future__ import annotations

import functools
import sys
from types import CodeType
from typing import Any
from typing import Callable
from typing import Iterator
from typing import Optional

import pytest


class _StubMonitoring:
    """Stand-in for ddtrace.internal.monitoring, which only imports on 3.15+."""

    def __init__(
        self,
        register_error: BaseException | None = None,
        unregister_error: BaseException | None = None,
    ) -> None:
        self.register_error: BaseException | None = register_error
        self.unregister_error: BaseException | None = unregister_error
        self.registered: list[tuple[CodeType, Any]] = []
        self.unregistered: list[tuple[CodeType, Any]] = []

    def register(self, code: CodeType, handler: Any) -> None:
        self.registered.append((code, handler))
        if self.register_error is not None:
            raise self.register_error

    def unregister(self, code: CodeType, handler: Any) -> None:
        self.unregistered.append((code, handler))
        if self.unregister_error is not None:
            raise self.unregister_error

    def get_tool_id(self) -> int:
        return 4


@pytest.fixture
def asyncio_module() -> Iterator[Any]:
    """Yield ddtrace.profiling._asyncio with its monitoring globals isolated."""
    from ddtrace.profiling import _asyncio

    previous_tool_id: int | None = _asyncio._monitoring_tool_id
    previous_handlers: dict[int, Callable[[object], None]] = _asyncio._py_return_handlers.copy()
    _asyncio._monitoring_tool_id = None
    _asyncio._py_return_handlers.clear()
    try:
        yield _asyncio
    finally:
        _asyncio._monitoring_tool_id = previous_tool_id
        _asyncio._py_return_handlers.clear()
        _asyncio._py_return_handlers.update(previous_handlers)


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
    policy_class: Optional[type[object]]
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


# TODO: the two 3.15-gated tests below never execute in CI today -- riotfile.py
# caps MAX_PYTHON_VERSION at 3.14 and only the smoke_test venv opts into 3.15,
# so the profiling suite is never collected on a 3.15 interpreter.
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
        assert _asyncio._py_return_handlers[id(function.__code__)] is callback
        assert _asyncio._monitoring_tool_id == 4
    finally:
        _asyncio._monitoring_handler = old_handler
        _asyncio._monitoring_tool_id = old_tool_id
        _asyncio._py_return_handlers.clear()
        _asyncio._py_return_handlers.update(old_handlers)


def test_register_return_hook_unwinds_a_failed_register(asyncio_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed register() leaves no handler entry behind and unregisters the code."""
    monitoring: _StubMonitoring = _StubMonitoring(register_error=RuntimeError("register exploded"))
    monkeypatch.setattr(asyncio_module, "_monitoring", monitoring, raising=False)
    dispatch: Any = asyncio_module._AsyncioReturnHookDispatch()

    def function() -> None:
        pass

    def callback(return_value: object) -> None:
        pass

    assert asyncio_module._do_register_return_hook(dispatch, function, callback) is False
    assert id(function.__code__) not in asyncio_module._py_return_handlers
    assert monitoring.unregistered == [(function.__code__, dispatch)]
    assert asyncio_module._monitoring_tool_id is None


def test_register_return_hook_handles_func_without_code_object(
    asyncio_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A func with no __code__ returns False instead of raising UnboundLocalError."""
    monitoring: _StubMonitoring = _StubMonitoring()
    monkeypatch.setattr(asyncio_module, "_monitoring", monitoring, raising=False)
    dispatch: Any = asyncio_module._AsyncioReturnHookDispatch()

    def callback(return_value: object) -> None:
        pass

    codeless: Any = functools.partial(lambda: None)
    assert not hasattr(codeless, "__code__")

    assert asyncio_module._do_register_return_hook(dispatch, codeless, callback) is False
    # len() is a builtin, so it has no __code__ either.
    assert asyncio_module._do_register_return_hook(dispatch, len, callback) is False
    assert asyncio_module._py_return_handlers == {}
    assert monitoring.registered == []
    assert monitoring.unregistered == []


def test_register_return_hook_swallows_a_failing_unregister(
    asyncio_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second failure while unwinding must not escape the helper."""
    monitoring: _StubMonitoring = _StubMonitoring(
        register_error=RuntimeError("register exploded"),
        unregister_error=RuntimeError("unregister exploded"),
    )
    monkeypatch.setattr(asyncio_module, "_monitoring", monitoring, raising=False)
    dispatch: Any = asyncio_module._AsyncioReturnHookDispatch()

    def function() -> None:
        pass

    def callback(return_value: object) -> None:
        pass

    assert asyncio_module._do_register_return_hook(dispatch, function, callback) is False
    assert monitoring.unregistered == [(function.__code__, dispatch)]
    assert id(function.__code__) not in asyncio_module._py_return_handlers


def test_on_py_return_invokes_the_registered_callback(asyncio_module: Any) -> None:
    dispatch: Any = asyncio_module._AsyncioReturnHookDispatch()
    assert dispatch.handlers is asyncio_module._py_return_handlers

    def function() -> None:
        pass

    seen: list[object] = []
    dispatch.handlers[id(function.__code__)] = seen.append

    dispatch.on_py_return(function.__code__, 0, "return value")

    assert seen == ["return value"]


def test_on_py_return_ignores_unregistered_code(asyncio_module: Any) -> None:
    dispatch: Any = asyncio_module._AsyncioReturnHookDispatch()

    def unrelated() -> None:
        pass

    dispatch.on_py_return(unrelated.__code__, 0, "return value")

    assert asyncio_module._py_return_handlers == {}


@pytest.mark.skipif(sys.version_info >= (3, 15), reason="the monitoring path is live on 3.15+")
def test_register_return_hook_is_gated_off_below_315(asyncio_module: Any) -> None:
    """Pin the gate's contract, not just the wrap() fallback it produces."""

    def function() -> None:
        pass

    def callback(return_value: object) -> None:
        pass

    assert asyncio_module._register_return_hook(function, callback) is False
    assert asyncio_module._py_return_handlers == {}
    assert asyncio_module._monitoring_tool_id is None
