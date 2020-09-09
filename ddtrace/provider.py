import abc
import sys
from ddtrace.vendor import six

from ddtrace.context import Context
from .internal.context_manager import DefaultContextManager, _DD_CONTEXTVAR


try:
    import asyncio
except ImportError:
    ASYNCIO = False
else:
    ASYNCIO = True


try:
    import gevent
except ImportError:
    GEVENT = False
else:
    GEVENT = True


USE_LEGACY = sys.version_info < (3, 7, 0)


def get_call_context():
    """
    Return the current active ``Context`` for this traced execution. This method is
    automatically called in the ``tracer.trace()``, but it can be used in the application
    code during manual instrumentation like::

        from ddtrace import tracer

        async def web_handler(request):
            context = tracer.get_call_context()
            # use the context if needed
            # ...

    This method makes use of a ``ContextProvider`` that is automatically set during the tracer
    initialization, or while using a library instrumentation.
    """
    if USE_LEGACY and ASYNCIO:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return None
        else:
            task = asyncio.Task.current_task(loop=loop)
            if task is None:
                return Context()

            ctx = getattr(task, "__dd_ctx", None)
            if ctx is not None:
                return ctx

            ctx = Context()
            setattr(task, "__dd_ctx", ctx)
            return ctx
    elif USE_LEGACY and GEVENT:
        current_g = gevent.getcurrent()
        if current_g is not None:
            return getattr(current_g, "__dd_ctx", None)
        else:
            ctx = Context()
            setattr(current_g, "__dd_ctx", ctx)
            return ctx
    else:
        ctx = _DD_CONTEXTVAR.get()
        if not ctx:
            ctx = Context()
        _DD_CONTEXTVAR.set(ctx)
        return ctx


def set_call_context(ctx):
    if USE_LEGACY and ASYNCIO:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            pass
        else:
            task = asyncio.Task.current_task(loop=loop)
            setattr(task, "__dd_ctx", ctx)
    elif USE_LEGACY and GEVENT:
        current_g = gevent.getcurrent()
        if current_g is not None:
            setattr(current_g, "__dd_ctx", ctx)
    else:
        _DD_CONTEXTVAR.set(ctx)


class BaseContextProvider(six.with_metaclass(abc.ABCMeta)):
    """
    A ``ContextProvider`` is an interface that provides the blueprint
    for a callable class, capable to retrieve the current active
    ``Context`` instance. Context providers must inherit this class
    and implement:
    * the ``active`` method, that returns the current active ``Context``
    * the ``activate`` method, that sets the current active ``Context``
    """

    @abc.abstractmethod
    def _has_active_context(self):
        pass

    @abc.abstractmethod
    def activate(self, context):
        pass

    @abc.abstractmethod
    def active(self):
        pass

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

    def __init__(self, reset_context_manager=True):
        self._local = DefaultContextManager(reset=reset_context_manager)

    def _has_active_context(self):
        """
        Check whether we have a currently active context.

        :returns: Whether we have an active context
        :rtype: bool
        """
        return self._local._has_active_context()

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
