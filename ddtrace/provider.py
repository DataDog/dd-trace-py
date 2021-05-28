import abc
from typing import Optional
from typing import Union

import six

from .context import Context
from .internal.compat import contextvars
from .span import Span


_DD_CONTEXTVAR = contextvars.ContextVar(
    "datadog_contextvar", default=None
)  # type: contextvars.ContextVar[Optional[Union[Context, Span]]]


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
    def activate(self, ctx):
        # type: (Optional[Union[Context, Span]]) -> None
        pass

    @abc.abstractmethod
    def active(self):
        # type: () -> Optional[Union[Context, Span]]
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
        # type: () -> None
        _DD_CONTEXTVAR.set(None)

    def _has_active_context(self):
        # type: () -> bool
        """Returns whether there is an active context in the current execution."""
        ctx = _DD_CONTEXTVAR.get()
        return ctx is not None

    def activate(self, ctx):
        # type: (Optional[Union[Span, Context]]) -> None
        """Makes the given context active in the current execution."""
        _DD_CONTEXTVAR.set(ctx)

    def _update_active(self, span):
        # type: (Span) -> Optional[Span]
        if span.finished:
            new_active = span  # type: Optional[Span]
            while new_active and new_active.finished:
                new_active = new_active._parent
            self.activate(new_active)
            return new_active
        return span

    def active(self):
        # type: () -> Optional[Union[Context, Span]]
        """Returns the active span or context for the current execution."""
        item = _DD_CONTEXTVAR.get()
        if isinstance(item, Span):
            return self._update_active(item)
        return item
