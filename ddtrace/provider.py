from .context import Context, ThreadLocalContext


class BaseContextProvider(object):
    """
    A ``ContextProvider`` is an interface that provides the blueprint
    for a callable class, capable to retrieve the current active
    ``Context`` instance. Context providers must inherit this class
    and implement:
        * the ``active`` method, that returns the current active ``Context``
        * the ``activate`` method, that sets the current active ``Context``
    """
    def activate(self, context):
        raise NotImplementedError

    def active(self):
        raise NotImplementedError

    def __call__(self, *args, **kwargs):
        """Method available for backward-compatibility. It proxies the call to
        ``self.active()`` and must not do anything more.
        """
        return self.active()


class DefaultContextProvider(BaseContextProvider):
    """
    Default context provider that retrieves all contexts from the current
    thread-local storage. It is suitable for synchronous programming and
    Python WSGI frameworks.
    """
    def __init__(self):
        self._local = ThreadLocalContext()

    def activate(self, context):
        """Makes the given ``context`` active, so that the provider calls
        the thread-local storage implementation.
        """
        return self._local.set(context)

    def active(self):
        """Returns the current active ``Context`` for this tracer. Returned
        ``Context`` must be thread-safe or thread-local for this specific
        implementation.
        """
        return self._local.get()


try:
    from contextvars import ContextVar
    ddtrace_context_var = ContextVar('ddtrace_context')

    class ContextVarContextProvider(DefaultContextProvider):
        """Context provider for ddtrace that uses ContextVars to store the current context for tracing.
        The AsyncioContextProvider built into ddtrace works for exclusively async applications where stuff that
        happens asynchronously happens exclusively asynchronously. For things like django-channels however,
        some spans happen synchronously and some asynchronously in the same logical unit of work. This usually happens
        through the @database_sync_to_async wrapper that Channels provides to move between the two worlds.

        To get traces to continue over the async/sync boundary (which is actually a thread boundary in the Channels
        implementation), the currently active tracing context must be passed across that boundary. Python 3.7 added
        support for ContextVars, which do exactly that as supported by Python itself. In combination with something
        like https://pypi.org/project/contextvars-executor/ , these ContextVars hold the same value in the code
        outside and inside of a new thread or a new asyncio.ThreadPool executor. They're safer than normal thread
        locals, and the preferred pattern for implementing context in async code after Python 3.7.

        So, if you are using Python 3.7, and you want a simple way to pass ddtrace contexts between a main
        asyncio threadloop and inner ThreadPooled executors, use ContextVarContextProvider and contextvars-executor
        to keep your contexts intact across thread boundaries.
        """
        def activate(self, context):
            ddtrace_context_var.set(context)
            return context

        def active(self):
            context = ddtrace_context_var.get(None)
            if not context:
                context = Context()
                ddtrace_context_var.set(context)
            return context

except ImportError:
    pass

