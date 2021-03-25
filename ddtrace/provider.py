import abc

from ddtrace.vendor import six

from .compat import contextvars
from .context import Context


_DD_CONTEXTVAR = contextvars.ContextVar("datadog_contextvar", default=None)


class BaseContextProvider(six.with_metaclass(abc.ABCMeta)):  # type: ignore[misc]
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
        # type: (Context) -> None
        pass

    @abc.abstractmethod
    def active(self):
        # type: () -> Context
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

    def __init__(self):
        # type: () -> None
        _DD_CONTEXTVAR.set(None)

    def _has_active_context(self):
        """
        Check whether we have a currently active context.

        :returns: Whether we have an active context
        :rtype: bool
        """
        ctx = _DD_CONTEXTVAR.get()
        return ctx is not None

    def activate(self, ctx):
        # type: (Context) -> None
        """Makes the given ``context`` active, so that the provider calls
        the thread-local storage implementation.
        """
        _DD_CONTEXTVAR.set(ctx)  # type: ignore[arg-type]

    def active(self):
        # type: () -> Context
        """Returns the current active ``Context`` for this tracer. Returned
        ``Context`` must be thread-safe or thread-local for this specific
        implementation.
        """
        ctx = _DD_CONTEXTVAR.get()
        if not ctx:
            ctx = Context()
            self.activate(ctx)

        return ctx
