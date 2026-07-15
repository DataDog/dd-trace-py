import sys
from types import ModuleType
from typing import Optional

import ddtrace
from ddtrace.internal import core
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.trace import Context


_PROFILING_STACK_MODULE = "ddtrace.internal.datadog.profiling.stack"


def _profiling_stack() -> Optional[ModuleType]:
    """
    Return the profiling stack module iff it is already loaded and available.

    We deliberately dont import it: when the profiler is disabled the module is
    never loaded, and the futures integration (enabled by default) won't pull in native
    extension
    """
    stack = sys.modules.get(_PROFILING_STACK_MODULE)
    if stack is not None and getattr(stack, "is_available", False):
        return stack
    return None


def _current_origin_task() -> "tuple[Optional[int], Optional[str]]":
    """
    Identity of the asyncio task submitting this work, as (task_id, task_name).

    `task_id` is `id(task)`, which matches the profiler's "task id" label (the
    task object's address), so executor worker-thread samples can be correlated
    back to the awaiting task's samples on the event-loop thread
    """
    if _profiling_stack() is None:
        return None, None
    asyncio = sys.modules.get("asyncio")
    if asyncio is None:
        # Not an asyncio application; there is no task to attribute this work to.
        return None, None
    try:
        task = asyncio.current_task()
    except RuntimeError:
        # No running event loop on the submitting thread.
        return None, None
    if task is None:
        return None, None
    return id(task), task.get_name()


def _wrap_submit(func, args, kwargs):
    """
    Wrap `Executor` method used to submit a work executed in another
    thread. This wrapper ensures that a new `Context` is created and
    properly propagated using an intermediate function.
    """
    # DEV: Be sure to propagate a Context and not a Span since we are crossing thread boundaries
    current_ctx: Optional[Context] = ddtrace.tracer.current_trace_context()
    llmobs_ctx: Optional[Context] = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
        "threading.submit", ()
    ).llmobs_ctx.value

    # Capture the submitting asyncio task so the profiler can link the
    # offloaded work back to it
    origin_task_id, origin_task_name = _current_origin_task()

    # Shallow copy for cross-thread handoff. current_trace_context() returns the
    # active Context (or the current span's .context), not a detached copy.
    if current_ctx is not None and current_ctx.trace_id is not None and current_ctx.span_id is not None:
        current_ctx = current_ctx.copy(current_ctx.trace_id, current_ctx.span_id)
        current_span = ddtrace.tracer.current_span()
        if current_span is not None:
            context_meta.attach_profiler_link(
                current_ctx,
                current_span._local_root.span_id,
                current_span._local_root.span_type,
            )

    # The target function can be provided as a kwarg argument "fn" or the first positional argument
    self = args[0]
    if "fn" in kwargs:
        fn = kwargs.pop("fn")
        fn_args = args[1:]
    else:
        fn, fn_args = args[1], args[2:]
    return func(self, _wrap_execution, (current_ctx, llmobs_ctx, origin_task_id, origin_task_name), fn, fn_args, kwargs)


def _wrap_execution(ctx: "tuple[Optional[Context], Optional[Context], Optional[int], Optional[str]]", fn, args, kwargs):
    """
    Intermediate target function that is executed in a new thread;
    it receives the original function with arguments and keyword
    arguments, including our tracing `Context`. The current context
    provider sets the Active context in a thread local storage
    variable because it's outside the asynchronous loop.
    """
    current_ctx, llmobs_ctx, origin_task_id, origin_task_name = ctx
    if llmobs_ctx is not None:
        core.dispatch("threading.execution", (llmobs_ctx,))

    stack = _profiling_stack() if origin_task_id is not None else None
    if stack is not None:
        stack.link_origin_task(origin_task_id, origin_task_name)
    try:
        if current_ctx is not None:
            with ddtrace.tracer._activate_context(current_ctx):
                return fn(*args, **kwargs)
        return fn(*args, **kwargs)
    finally:
        if stack is not None:
            stack.unlink_origin_task()
