"""Label the samples of worker threads that run a function inside a copied contextvars.Context.

anyio.to_thread.run_sync and asyncio.to_thread copy the caller's Context and run the function on a worker thread with
context.run(func, ...). Entering that Context fires no span activation event, so without help the worker's thread key
would not hold the span the function runs in. The thread key is the span link entry, keyed by thread, that the sampler
uses to label a plain thread's samples. For example, a request handler that calls
await anyio.to_thread.run_sync(parse) inside span S would get worker samples with no span.

The hooks here replace the user function with partial(run_in_worker, func). run_in_worker runs inside the copied
Context, publishes the span the tracer stores there to the worker's thread key, runs the function, and then clears the
key, so a pooled worker does not keep the request's span for its next call.
"""

from functools import partial
from types import ModuleType
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.datadog.profiling._types import ProfilerTracer
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.native._native import DD_CONTEXTVAR
from ddtrace.internal.wrapping import wrap
from ddtrace.profiling import _span_links


log = get_logger(__name__)

# The tracer of the running stack collector. Its context provider decides whether the item stored in the Context is
# what the tracer would treat as active (context_meta.inheritance_mode).
_tracer: Optional[ProfilerTracer] = None
# Names of the modules whose hook install() has registered.
_registered_hooks: set[str] = set()


def run_in_worker(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run func on the current worker thread with the span of the current Context published to the thread key.

    func is positional-only, because kwargs belong to func and can contain a "func" key, as in
    asyncio.to_thread(f, func=42). func is called exactly once, outside every profiler try block, so its result and its
    exceptions pass through unchanged.
    """
    attempted = False
    try:
        # The lifecycle token is odd while the stack collector runs. It is read before any tracer state, so a stop
        # that happens after this point makes the write below a no-op.
        token = stack.lifecycle()
        tracer = _tracer
        if token & 1 and tracer is not None:
            provider = tracer.context_provider
            # With a provider that has its own active() rule, the item stored in the Context is not a reliable
            # answer, so the thread key is left to the activation events, for example the futures integration's.
            if context_meta.inheritance_mode(provider):
                attempted = True
                item = context_meta.resolve(DD_CONTEXTVAR.get())
                _span_links.publish(provider, token, True, 0, 0, item)
    except Exception:  # nosec B110
        pass
    try:
        return func(*args, **kwargs)
    finally:
        # Clean up even if the write raised partway, because it may have landed before the error.
        if attempted:
            try:
                stack.link(stack.lifecycle(), True, 0, 0, None)
            except Exception:  # nosec B110
                pass


def _wrap_worker_function(f: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Bytecode wrapper for helpers whose first parameter, func, is the function to run in a copied Context.

    The bytecode wrapper passes the helper's parameters as they are bound, so args[0] is always func, even when the
    caller passed it as func=. That holds for asyncio.to_thread(func, /, *args, **kwargs) and
    anyio.to_thread.run_sync(func, *args, ...).
    """
    try:
        args = (partial(run_in_worker, args[0]),) + args[1:]
    except Exception:  # nosec B110
        pass
    return f(*args, **kwargs)


def _wrap_asyncio_to_thread(threads: ModuleType) -> None:
    try:
        wrap(threads.to_thread, _wrap_worker_function)
    except Exception:
        log.debug("Failed to wrap asyncio.to_thread for the stack profiler", exc_info=True)


def _wrap_anyio_run_sync(to_thread: ModuleType) -> None:
    try:
        wrap(to_thread.run_sync, _wrap_worker_function)
    except Exception:
        log.debug("Failed to wrap anyio.to_thread.run_sync for the stack profiler", exc_info=True)


_WORKER_HOOKS: tuple[tuple[str, Callable[[ModuleType], None]], ...] = (
    ("asyncio.threads", _wrap_asyncio_to_thread),
    ("anyio.to_thread", _wrap_anyio_run_sync),
)


def install(tracer: ProfilerTracer) -> None:
    """Use tracer for worker threads, and wrap the worker helpers once, now or when their module is imported.

    The module hooks run right away for a module that is already imported, so the order in which the profiler and
    anyio are imported does not matter. The bytecode wrapper changes the function's code in place, so it also covers
    names bound earlier, as in "from anyio.to_thread import run_sync".
    """
    global _tracer

    _tracer = tracer
    for module, hook in _WORKER_HOOKS:
        if module in _registered_hooks:
            continue
        # A failure here must not stop the profiler from starting. The module is marked only after its hook is
        # registered, so the next start retries it. register_module_hook does not deduplicate, and a second
        # registration would wrap the helper twice, so a marked module is skipped.
        try:
            ModuleWatchdog.register_module_hook(module, hook)
        except Exception:
            log.debug("Failed to register the stack profiler hook for %s", module, exc_info=True)
        else:
            _registered_hooks.add(module)
