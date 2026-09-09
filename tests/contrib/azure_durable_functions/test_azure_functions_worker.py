import asyncio
import sys
from types import ModuleType
from types import SimpleNamespace

import azure.durable_functions as durable_functions
import azure.functions as azure_functions

from ddtrace.contrib.internal.azure_functions._worker import get_current_invocation_carrier
from ddtrace.contrib.internal.azure_functions._worker import patch_worker_context
from ddtrace.contrib.internal.azure_functions._worker import unpatch_worker_context
from ddtrace.internal.module import ModuleWatchdog


TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
TRACESTATE = "dd=s:1"


def _invocation_context():
    return SimpleNamespace(
        trace_context=SimpleNamespace(
            trace_parent=TRACEPARENT,
            trace_state=TRACESTATE,
        )
    )


def _assert_current_carrier():
    assert get_current_invocation_carrier() == {
        "traceparent": TRACEPARENT,
        "tracestate": TRACESTATE,
    }
    return "ok"


def _allow_worker_unpatch(monkeypatch):
    monkeypatch.setattr(azure_functions, "_datadog_patch", False, raising=False)
    monkeypatch.setattr(durable_functions, "_datadog_patch", False, raising=False)


def test_classic_worker_context_is_scoped_to_sync_and_async_execution(monkeypatch):
    class Dispatcher:
        def _run_sync_func(self, invocation_id, context, func, params):
            return func(**params)

        async def _run_async_func(self, context, func, params):
            return await func(**params)

    worker = ModuleType("azure_functions_worker.dispatcher")
    worker.Dispatcher = Dispatcher
    monkeypatch.setitem(sys.modules, worker.__name__, worker)
    _allow_worker_unpatch(monkeypatch)

    patch_worker_context()
    try:
        dispatcher = Dispatcher()
        assert dispatcher._run_sync_func("invocation-id", _invocation_context(), _assert_current_carrier, {}) == "ok"
        assert get_current_invocation_carrier() is None

        async def invoke_async():
            async def handler():
                return _assert_current_carrier()

            result = await dispatcher._run_async_func(_invocation_context(), handler, {})
            assert get_current_invocation_carrier() is None
            return result

        assert asyncio.run(invoke_async()) == "ok"
    finally:
        unpatch_worker_context()


def test_v2_worker_context_is_captured_before_async_and_sync_execution(monkeypatch):
    worker = ModuleType("azure_functions_runtime.handle_event")
    worker.get_context = lambda context: context
    worker.run_sync_func = lambda invocation_id, context, func, params: func(**params)

    async def invocation_request(context, func):
        worker.get_context(context)
        return await func()

    worker.invocation_request = invocation_request
    monkeypatch.setitem(sys.modules, worker.__name__, worker)
    _allow_worker_unpatch(monkeypatch)

    patch_worker_context()
    try:

        async def invoke_async():
            async def handler():
                return _assert_current_carrier()

            result = await worker.invocation_request(_invocation_context(), handler)
            assert get_current_invocation_carrier() is None
            return result

        assert asyncio.run(invoke_async()) == "ok"

        assert worker.run_sync_func("invocation-id", _invocation_context(), _assert_current_carrier, {}) == "ok"
        assert get_current_invocation_carrier() is None
    finally:
        unpatch_worker_context()


def test_v2_worker_context_is_cleared_when_dispatch_fails(monkeypatch):
    worker = ModuleType("azure_functions_runtime.handle_event")
    worker.get_context = lambda context: context
    worker.run_sync_func = lambda invocation_id, context, func, params: func(**params)

    async def invocation_request(context):
        worker.get_context(context)
        raise RuntimeError("dispatch failed")

    worker.invocation_request = invocation_request
    monkeypatch.setitem(sys.modules, worker.__name__, worker)
    _allow_worker_unpatch(monkeypatch)

    patch_worker_context()
    try:

        async def invoke_async():
            try:
                await worker.invocation_request(_invocation_context())
            except RuntimeError:
                pass
            assert get_current_invocation_carrier() is None

        asyncio.run(invoke_async())
    finally:
        unpatch_worker_context()


def test_worker_modules_are_patched_when_imported_after_the_integration(monkeypatch):
    _allow_worker_unpatch(monkeypatch)
    unpatch_worker_context()
    monkeypatch.delitem(sys.modules, "azure_functions_worker.dispatcher", raising=False)
    monkeypatch.delitem(sys.modules, "azure_functions_runtime.handle_event", raising=False)
    registered = {}
    unregistered = set()

    monkeypatch.setattr(
        ModuleWatchdog,
        "register_module_hook",
        lambda module_name, hook: registered.setdefault(module_name, hook),
    )
    monkeypatch.setattr(
        ModuleWatchdog,
        "unregister_module_hook",
        lambda module_name, hook: unregistered.add((module_name, hook)),
    )

    patch_worker_context()
    assert set(registered) == {
        "azure_functions_worker.dispatcher",
        "azure_functions_runtime.handle_event",
        "azure_functions_runtime.bindings.context",
        "azure_functions_runtime.utils.executor",
    }

    class Dispatcher:
        def _run_sync_func(self, invocation_id, context, func, params):
            return func(**params)

        async def _run_async_func(self, context, func, params):
            return await func(**params)

    worker = ModuleType("azure_functions_worker.dispatcher")
    worker.Dispatcher = Dispatcher
    registered[worker.__name__](worker)
    assert hasattr(Dispatcher._run_sync_func, "__wrapped__")
    assert hasattr(Dispatcher._run_async_func, "__wrapped__")

    unpatch_worker_context()
    assert {module_name for module_name, _ in unregistered} == set(registered)


def test_v2_worker_canonical_context_and_executor_modules_are_patched(monkeypatch):
    context_module = ModuleType("azure_functions_runtime.bindings.context")
    context_module.get_context = lambda context: context
    executor_module = ModuleType("azure_functions_runtime.utils.executor")
    executor_module.run_sync_func = lambda invocation_id, context, func, params: func(**params)
    monkeypatch.setitem(sys.modules, context_module.__name__, context_module)
    monkeypatch.setitem(sys.modules, executor_module.__name__, executor_module)
    _allow_worker_unpatch(monkeypatch)

    patch_worker_context()
    try:
        context_module.get_context(_invocation_context())
        assert get_current_invocation_carrier() == {"traceparent": TRACEPARENT, "tracestate": TRACESTATE}
        assert (
            executor_module.run_sync_func("invocation-id", _invocation_context(), _assert_current_carrier, {}) == "ok"
        )
        assert get_current_invocation_carrier() == {"traceparent": TRACEPARENT, "tracestate": TRACESTATE}
    finally:
        unpatch_worker_context()
