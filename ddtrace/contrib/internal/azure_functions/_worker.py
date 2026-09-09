import contextvars
import sys
from types import ModuleType
from typing import Any
from typing import Callable
from typing import Optional

from wrapt import wrap_function_wrapper as _w

from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog


log = get_logger(__name__)
_CURRENT_INVOCATION_CARRIER: contextvars.ContextVar[Optional[dict[str, str]]] = contextvars.ContextVar(
    "datadog_azure_functions_invocation_carrier", default=None
)
_PATCHED_TARGETS: list[tuple[Any, str]] = []
_REGISTERED_MODULE_HOOKS: set[str] = set()


def _carrier_from_invocation_context(invocation_context: Any) -> Optional[dict[str, str]]:
    trace_context = getattr(invocation_context, "trace_context", None)
    traceparent = getattr(trace_context, "trace_parent", None)
    if not traceparent:
        return None

    carrier = {"traceparent": traceparent}
    tracestate = getattr(trace_context, "trace_state", None)
    if tracestate:
        carrier["tracestate"] = tracestate
    return carrier


def get_current_invocation_carrier() -> Optional[dict[str, str]]:
    return _CURRENT_INVOCATION_CARRIER.get()


def _context_from_args(args: tuple[Any, ...], kwargs: dict[str, Any], position: int) -> Any:
    return kwargs.get("context", args[position] if len(args) > position else None)


def _capture_context(wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    invocation_context = wrapped(*args, **kwargs)
    _CURRENT_INVOCATION_CARRIER.set(_carrier_from_invocation_context(invocation_context))
    return invocation_context


def _run_sync_with_context(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    invocation_context = _context_from_args(args, kwargs, 1)
    token = _CURRENT_INVOCATION_CARRIER.set(_carrier_from_invocation_context(invocation_context))
    try:
        return wrapped(*args, **kwargs)
    finally:
        _CURRENT_INVOCATION_CARRIER.reset(token)


async def _run_async_with_context(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    invocation_context = _context_from_args(args, kwargs, 0)
    token = _CURRENT_INVOCATION_CARRIER.set(_carrier_from_invocation_context(invocation_context))
    try:
        return await wrapped(*args, **kwargs)
    finally:
        _CURRENT_INVOCATION_CARRIER.reset(token)


async def _run_v2_invocation_with_context(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    token = _CURRENT_INVOCATION_CARRIER.set(None)
    try:
        return await wrapped(*args, **kwargs)
    finally:
        _CURRENT_INVOCATION_CARRIER.reset(token)


def _resolve_owner(module: ModuleType, path: str) -> tuple[Any, str]:
    owner: Any = module
    parts = path.split(".")
    for part in parts[:-1]:
        owner = getattr(owner, part)
    return owner, parts[-1]


def _patch_target(module: ModuleType, path: str, wrapper: Callable[..., Any]) -> None:
    try:
        owner, attribute = _resolve_owner(module, path)
        if not hasattr(owner, attribute):
            return
        if any(
            patched_owner is owner and patched_attribute == attribute
            for patched_owner, patched_attribute in _PATCHED_TARGETS
        ):
            return
        _w(module, path, wrapper)
        _PATCHED_TARGETS.append((owner, attribute))
    except Exception:
        log.debug("Unable to patch Azure Functions worker context target %s", path, exc_info=True)


def _patch_classic_worker(module: ModuleType) -> None:
    _patch_target(module, "Dispatcher._run_sync_func", _run_sync_with_context)
    _patch_target(module, "Dispatcher._run_async_func", _run_async_with_context)


def _patch_v2_worker(module: ModuleType) -> None:
    _patch_target(module, "get_context", _capture_context)
    _patch_target(module, "run_sync_func", _run_sync_with_context)
    _patch_target(module, "invocation_request", _run_v2_invocation_with_context)


_WORKER_MODULE_HOOKS = {
    "azure_functions_worker.dispatcher": _patch_classic_worker,
    "azure_functions_runtime.handle_event": _patch_v2_worker,
}


def patch_worker_context() -> None:
    # AIDEV-NOTE: When Azure supplies a W3C carrier it lives on the invocation
    # Context, which Durable handlers do not receive because their `context`
    # parameter is already a trigger binding. These guarded private hooks cover the
    # classic worker and the Python 3.13 v2 runtime without enabling OTel export.
    # Some Durable trigger types (notably entities) may receive no host carrier.
    for module_name, hook in _WORKER_MODULE_HOOKS.items():
        if module_name not in _REGISTERED_MODULE_HOOKS:
            ModuleWatchdog.register_module_hook(module_name, hook)
            _REGISTERED_MODULE_HOOKS.add(module_name)


def unpatch_worker_context() -> None:
    azure_functions = sys.modules.get("azure.functions")
    durable_functions = sys.modules.get("azure.durable_functions")
    if getattr(azure_functions, "_datadog_patch", False) or getattr(durable_functions, "_datadog_patch", False):
        return

    while _REGISTERED_MODULE_HOOKS:
        module_name = _REGISTERED_MODULE_HOOKS.pop()
        ModuleWatchdog.unregister_module_hook(module_name, _WORKER_MODULE_HOOKS[module_name])

    while _PATCHED_TARGETS:
        owner, attribute = _PATCHED_TARGETS.pop()
        _u(owner, attribute)
    _CURRENT_INVOCATION_CARRIER.set(None)
