import abc
from typing import Any
from typing import Callable
from typing import Optional
from typing import Union

import six

from . import _hooks
from .context import Context
from .internal.compat import contextvars
from .internal.logger import get_logger
from .span import Span


log = get_logger(__name__)


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

    def __init__(self):
        # type: (...) -> None
        self._hooks = _hooks.Hooks()

    @abc.abstractmethod
    def _has_active_context(self):
        pass

    @abc.abstractmethod
    def activate(self, ctx):
        # type: (Optional[Union[Context, Span]]) -> None
        self._hooks.emit(self.activate, ctx)

    @abc.abstractmethod
    def active(self):
        # type: () -> Optional[Union[Context, Span]]
        pass

    def _on_activate(self, func):
        # type: (Callable[[Optional[Union[Span, Context]]], Any]) -> Callable[[Optional[Union[Span, Context]]], Any]
        """Register a function to execute when a span is activated.

        Can be used as a decorator.

        :param func: The function to call when a span is activated.
                     The activated span will be passed as argument.
        """
        self._hooks.register(self.activate, func)
        return func

    def _deregister_on_activate(self, func):
        # type: (Callable[[Optional[Union[Span, Context]]], Any]) -> Callable[[Optional[Union[Span, Context]]], Any]
        """Unregister a function registered to execute when a span is activated.

        Can be used as a decorator.

        :param func: The function to stop calling when a span is activated.
        """

        self._hooks.deregister(self.activate, func)
        return func

    def __call__(self, *args, **kwargs):
        """Method available for backward-compatibility. It proxies the call to
        ``self.active()`` and must not do anything more.
        """
        return self.active()


class DatadogContextMixin(object):
    """Mixin that provides active span updating suitable for synchronous
    and asynchronous executions.
    """

    def activate(self, ctx):
        # type: (Optional[Union[Context, Span]]) -> None
        raise NotImplementedError

    def _update_active(self, span):
        # type: (Span) -> Optional[Span]
        """Updates the active span in an executor.

        The active span is updated to be the span's parent if the span has
        finished until an unfinished span is found.
        """
        if span.finished:
            new_active = span  # type: Optional[Span]
            while new_active and new_active.finished:
                new_active = new_active._parent
            self.activate(new_active)
            return new_active
        return span


class DefaultContextProvider(BaseContextProvider, DatadogContextMixin):
    """Context provider that retrieves contexts from a context variable.

    It is suitable for synchronous programming and for asynchronous executors
    that support contextvars.
    """

    def __init__(self):
        # type: () -> None
        super(DefaultContextProvider, self).__init__()
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
        super(DefaultContextProvider, self).activate(ctx)

    def active(self):
        # type: () -> Optional[Union[Context, Span]]
        """Returns the active span or context for the current execution."""
        item = _DD_CONTEXTVAR.get()
        if isinstance(item, Span):
            return self._update_active(item)
        return item
