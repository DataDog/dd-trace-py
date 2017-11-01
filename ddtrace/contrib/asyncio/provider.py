import asyncio

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

    This Context Provider inherits from ``DefaultContextProvider`` because
    it uses a thread-local storage when the ``Context`` is propagated to
    a different thread, than the one that is running the async loop.
    """
    def activate(self, context, loop=None):
        """Sets the scoped ``Context`` for the current running ``Task``.
        """
        try:
            loop = loop or asyncio.get_event_loop()
        except RuntimeError:
            # detects if a loop is available in the current thread;
            # This happens when a new thread is created from the one that is running
            # the async loop
            self._local.set(context)
            return context

        # the current unit of work (if tasks are used)
        task = asyncio.Task.current_task(loop=loop)
        setattr(task, CONTEXT_ATTR, context)
        return context

    def active(self, loop=None):
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
