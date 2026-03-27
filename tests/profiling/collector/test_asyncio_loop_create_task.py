from __future__ import annotations

import asyncio
import functools
import importlib
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType
import types
import typing


class _StackStub(object):
    is_available = True
    failure_msg = ""

    def __init__(self) -> None:
        self.init_calls: list[tuple[typing.Any, typing.Any]] = []
        self.tracked_loops: list[
            tuple[int, typing.Optional[asyncio.AbstractEventLoop]]
        ] = []
        self.weak_links: list[
            tuple[asyncio.Task[typing.Any], asyncio.Task[typing.Any]]
        ] = []

    def init_asyncio(self, scheduled_tasks: typing.Any, eager_tasks: typing.Any) -> None:
        self.init_calls.append((scheduled_tasks, eager_tasks))

    def track_asyncio_loop(
        self, thread_id: int, loop: typing.Optional[asyncio.AbstractEventLoop]
    ) -> None:
        self.tracked_loops.append((thread_id, loop))

    def link_tasks(
        self, parent: asyncio.Task[typing.Any], child: asyncio.Future[typing.Any]
    ) -> None:
        pass

    def weak_link_tasks(
        self, parent: asyncio.Task[typing.Any], child: asyncio.Future[typing.Any]
    ) -> None:
        self.weak_links.append((parent, typing.cast(asyncio.Task[typing.Any], child)))

    def set_uvloop_mode(self, thread_id: int, uvloop_mode: bool) -> None:
        pass


class _WrapperRegistry(object):
    def __init__(self) -> None:
        self._patches: list[tuple[typing.Any, str, typing.Any]] = []

    def wrap(
        self, target: typing.Any, wrapper: typing.Callable[..., typing.Any]
    ) -> typing.Any:
        module = importlib.import_module(target.__module__)
        owner = module
        qualname_parts = target.__qualname__.split(".")
        for part in qualname_parts[:-1]:
            owner = getattr(owner, part)

        attribute = qualname_parts[-1]
        original = getattr(owner, attribute)

        @functools.wraps(original)
        def patched(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            return wrapper(original, args, kwargs)

        setattr(owner, attribute, patched)
        self._patches.append((owner, attribute, original))
        return patched

    def restore(self) -> None:
        for owner, attribute, original in reversed(self._patches):
            setattr(owner, attribute, original)


class _ModuleWatchdogStub(object):
    callbacks: dict[str, typing.Callable[[ModuleType], None]] = {}

    @classmethod
    def after_module_imported(
        cls, module_name: str
    ) -> typing.Callable[
        [typing.Callable[[ModuleType], None]],
        typing.Callable[[ModuleType], None],
    ]:
        def decorator(
            callback: typing.Callable[[ModuleType], None],
        ) -> typing.Callable[[ModuleType], None]:
            cls.callbacks[module_name] = callback
            return callback

        return decorator


class _LoadedAsyncioModule(typing.NamedTuple):
    module: ModuleType
    stack: _StackStub
    cleanup: typing.Callable[[], None]


def _get_argument_value(
    args: tuple[typing.Any, ...],
    kwargs: dict[str, typing.Any],
    pos: int,
    kw: str,
    optional: bool = False,
) -> typing.Any:
    if kw in kwargs:
        return kwargs[kw]
    if len(args) > pos:
        return args[pos]
    if optional:
        return None
    raise AssertionError("argument lookup failed")


def _load_asyncio_module_under_test(
    initialize_asyncio: bool = True,
) -> _LoadedAsyncioModule:
    stack = _StackStub()
    wrappers = _WrapperRegistry()
    _ModuleWatchdogStub.callbacks = {}

    fake_modules: dict[str, ModuleType] = {}

    def package(name: str) -> ModuleType:
        module = ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        fake_modules[name] = module
        return module

    package("ddtrace")
    package("ddtrace.internal")
    package("ddtrace.internal.datadog")
    package("ddtrace.internal.datadog.profiling")
    package("ddtrace.internal.settings")

    unpatched_module = ModuleType("ddtrace.internal._unpatched")
    unpatched_module._threading = threading
    fake_modules[unpatched_module.__name__] = unpatched_module

    stack_module = ModuleType("ddtrace.internal.datadog.profiling.stack")
    stack_module.is_available = stack.is_available
    stack_module.failure_msg = stack.failure_msg
    stack_module.init_asyncio = stack.init_asyncio
    stack_module.track_asyncio_loop = stack.track_asyncio_loop
    stack_module.link_tasks = stack.link_tasks
    stack_module.weak_link_tasks = stack.weak_link_tasks
    stack_module.set_uvloop_mode = stack.set_uvloop_mode
    fake_modules[stack_module.__name__] = stack_module

    module_watchdog_module = ModuleType("ddtrace.internal.module")
    module_watchdog_module.ModuleWatchdog = _ModuleWatchdogStub
    fake_modules[module_watchdog_module.__name__] = module_watchdog_module

    profiling_settings_module = ModuleType("ddtrace.internal.settings.profiling")
    profiling_settings_module.config = types.SimpleNamespace(
        stack=types.SimpleNamespace(enabled=True, uvloop=True)
    )
    fake_modules[profiling_settings_module.__name__] = profiling_settings_module

    utils_module = ModuleType("ddtrace.internal.utils")
    utils_module.get_argument_value = _get_argument_value
    fake_modules[utils_module.__name__] = utils_module

    wrapping_module = ModuleType("ddtrace.internal.wrapping")
    wrapping_module.wrap = wrappers.wrap
    fake_modules[wrapping_module.__name__] = wrapping_module

    originals = {name: sys.modules.get(name) for name in fake_modules}
    sys.modules.update(fake_modules)

    module_path = (
        Path(__file__).resolve().parents[3] / "ddtrace" / "profiling" / "_asyncio.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_profiling_asyncio_under_test", module_path
    )
    assert spec is not None and spec.loader is not None
    module_under_test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module_under_test)

    if initialize_asyncio:
        if sys.version_info >= (3, 11):
            importlib.import_module("asyncio.taskgroups")

        _ModuleWatchdogStub.callbacks["asyncio"](asyncio)

    def cleanup() -> None:
        wrappers.restore()
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    return _LoadedAsyncioModule(module=module_under_test, stack=stack, cleanup=cleanup)


def test_loop_create_task_weak_links_to_current_task() -> None:
    loaded_module = _load_asyncio_module_under_test()
    loop = asyncio.new_event_loop()

    async def child() -> None:
        await asyncio.sleep(0)

    async def parent() -> asyncio.Task[None]:
        nested_task = loop.create_task(child(), name="child-task")
        await nested_task
        return nested_task

    try:
        asyncio.set_event_loop(loop)
        parent_task = loop.create_task(parent(), name="parent-task")
        nested_task = loop.run_until_complete(parent_task)
    finally:
        asyncio.set_event_loop(None)
        loop.close()
        loaded_module.cleanup()

    assert loaded_module.stack.weak_links == [(parent_task, nested_task)]
