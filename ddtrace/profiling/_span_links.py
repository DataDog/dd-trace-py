"""Publish the span an execution unit runs to the stack profiler's span links."""

from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.native._native import SpanData


def publish(
    provider: object,
    token: int,
    thread: bool,
    task_id: int,
    greenlet_id: int,
    item: context_meta.ActiveItem,
) -> None:
    """Write item to the current thread's key if thread is true, and to the task and greenlet keys whose id is not 0.

    token is the stack.lifecycle() value the caller read before it read any tracer state. item can finish on another
    thread before the write lands, and the finish listener then misses the new keys. So after the write, this
    re-checks item and moves the keys off it if it has finished.
    """
    stack.link(token, thread, task_id, greenlet_id, context_meta.span_info(item))
    retarget_finished(provider, token, item)


def retarget_finished(provider: object, token: int, item: context_meta.ActiveItem) -> None:
    """While item has finished, move every span link that holds it to what provider.active() would return instead.

    With DefaultContextProvider that is usually the nearest unfinished ancestor of item. If provider does not follow
    DefaultContextProvider (see context_meta.inheritance_mode), the links are cleared instead. The replacement may
    itself have finished in the meantime, so this repeats until it reaches an unfinished span, a Context or no span.
    """
    while isinstance(item, SpanData) and item.finished:
        replacement = context_meta.resolve(item) if context_meta.inheritance_mode(provider) else None
        stack.retarget(token, item.span_id, context_meta.span_info(replacement))
        item = replacement
