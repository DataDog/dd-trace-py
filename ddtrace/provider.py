import abc
from typing import Optional

from ddtrace.vendor import six

from ddtrace.compat import contextvars, current_thread, asyncio_current_task
from ddtrace.internal import _rand


_DD_CONTEXTVAR = contextvars.ContextVar("datadog_tracing_contextvar", default=None)


def current_execution_id():
    # type: () -> Optional[int]
    """Return a unique identifier for the current execution."""
    obj = asyncio_current_task()
    if not obj:
        obj = current_thread()

    if obj:
        ident = getattr(obj, "__dd_id", None)
        if ident:
            return ident
        else:
            ident = _rand.rand64bits(check_pid=False)
            setattr(obj, "__dd_id", ident)
            return ident
    raise RuntimeError("Unable to get an execution id")


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
        pass

    def _has_active_context(self):
        """
        Check whether we have a currently active context.

        :returns: Whether we have an active context
        :rtype: bool
        """
        ctx = _DD_CONTEXTVAR.get()
        return ctx is not None

    def activate(self, context):
        """Makes the given ``context`` active, so that the provider calls
        the thread-local storage implementation.
        """
        return _DD_CONTEXTVAR.set(context)

    def active(self):
        """Returns the current active ``Context`` for this tracer. Returned
        ``Context`` must be thread-safe or thread-local for this specific
        implementation.
        """
        return _DD_CONTEXTVAR.get()
