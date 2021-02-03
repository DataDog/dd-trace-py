import abc
from typing import Optional

from ddtrace.vendor import six

from ddtrace import Span
from ddtrace.context import Context
from ddtrace.compat import contextvars


_DD_CONTEXTVAR = contextvars.ContextVar("datadog_tracing_contextvar", default=None)


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
    Default context provider that retrieves all contexts from a context variable.

    It is suitable for synchronous programming and for asynchronous executors
    that support contextvars.
    """

    def __init__(self):
        pass

    def _has_active_context(self):
        # type: () -> bool
        """Returns whether there is an active context in the current execution."""
        ctx = _DD_CONTEXTVAR.get()
        return ctx is not None

    def activate(self, context):
        # type: (Optional[Span, Context]) -> None
        """Makes the given context active in the current execution."""
        _DD_CONTEXTVAR.set(context)

    def active(self):
        """Returns the active context for the current execution."""
        return _DD_CONTEXTVAR.get()
