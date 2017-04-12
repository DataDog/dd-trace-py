import asyncio
from asyncio.base_events import BaseEventLoop

from ...context import Context
from ...provider import DefaultContextProvider


# Task attribute used to set/get the Context instance
CONTEXT_ATTR = '__datadog_context'


class AsyncioContextProvider(DefaultContextProvider):
    """
    Context provider that retrieves all contexts for the current asyncio
    execution. It must be used in asynchronous programming that relies
    in the built-in ``asyncio`` library. Framework instrumentation that
    is built on top of the ``asyncio`` library, can use this provider.
    """
    def __call__(self, loop=None):
        """
        Returns the scoped Context for this execution flow. The ``Context`` uses
        the current task as a carrier so if a single task is used for the entire application,
        the context must be handled separately.
        """
        try:
            loop = loop or asyncio.get_event_loop()
        except RuntimeError:
            # handles RuntimeError: There is no current event loop in thread 'MainThread'
            # it happens when it's not possible to get the current event loop.
            # It's possible that a different Executor is handling a different Thread that
            # works with blocking code. In that case, we fallback to a thread-local Context.
            return self._local.get()

        # the current unit of work (if tasks are used)
        task = asyncio.Task.current_task(loop=loop)
        if task is None:
            # providing a detached Context from the current Task, may lead to
            # wrong traces. This defensive behavior grants that a trace can
            # still be built without raising exceptions
            return Context()

        ctx = getattr(task, CONTEXT_ATTR, None)
        if ctx is not None:
            # return the active Context for this task (if any)
            return ctx

        # create a new Context using the Task as a Context carrier
        ctx = Context()
        setattr(task, CONTEXT_ATTR, ctx)
        return ctx


def enable_task_linking() -> None:
    """ This method will enable spawned tasks to share the same context as their base task context """
    # Monkeypatch BaseEventLoop.create_task to associate task contexts to spawned tasks
    _orig_create_task = BaseEventLoop.create_task

    def _create_task(*args, **kwargs):
        new_task = _orig_create_task(*args, **kwargs)
        current_task = asyncio.Task.current_task()

        ctx = getattr(current_task, CONTEXT_ATTR, None)
        if ctx:
            # current task has a context, so link the two
            setattr(new_task, CONTEXT_ATTR, ctx)

        return new_task

    BaseEventLoop.create_task = _create_task

enable_task_linking()
