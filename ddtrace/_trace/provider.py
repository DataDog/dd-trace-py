import abc
import contextvars
from typing import Any
from typing import Callable
from typing import Optional
from typing import Union

from ddtrace import _hooks
from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class ContextVarManager:
    """
    In the implementation of ContextVar, when a key is re-associated with a new value the underlying HAMT must clone
    a level of the tree in order to maintain immutability. This operation requires de-referencing pointers to Python
    objects stored in the Context, which typically includes objects not created or managed by this library. It's
    possible for such objects to have mis-managed reference counts (speculatively:  in order to convert their
    ContextVar storage from a strong to a weak reference. When such objects are de-referenced--as they would be when
    a reassoc from this code forces a clone--it could cause heap corruption or a segmentation fault.

    Accordingly, we try to prevent reassoc events when possible by storing a long-lived wrapper object and only setting
    the target value within that object.

    tl;dr: Don't call `set()` on a context var a second time.
    """

    def get(self) -> Optional[Union[Context, Span]]:
        return _DD_ACTUAL_CONTEXTVAR.get().value

    def set(self, value: Optional[Union[Context, Span]]) -> None:
        _DD_ACTUAL_CONTEXTVAR.get().value = value


class ContextVarWrapper:
    def __init__(self) -> None:
        self.value: Optional[Union[Context, Span]] = None


# Initialize the context manager
_DD_ACTUAL_CONTEXTVAR = contextvars.ContextVar[ContextVarWrapper]("datadog_contextvar", default=ContextVarWrapper())
_DD_CONTEXTVAR = ContextVarManager()


class BaseContextProvider(metaclass=abc.ABCMeta):
    """
    A ``ContextProvider`` is an interface that provides the blueprint
    for a callable class, capable to retrieve the current active
    ``Context`` instance. Context providers must inherit this class
    and implement:
    * the ``active`` method, that returns the current active ``Context``
    * the ``activate`` method, that sets the current active ``Context``
    """

    def __init__(self) -> None:
        self._hooks = _hooks.Hooks()

    @abc.abstractmethod
    def _has_active_context(self) -> bool:
        pass

    @abc.abstractmethod
    def activate(self, ctx: Optional[Union[Context, Span]]) -> None:
        self._hooks.emit(self.activate, ctx)

    @abc.abstractmethod
    def active(self) -> Optional[Union[Context, Span]]:
        pass

    def _on_activate(
        self, func: Callable[[Optional[Union[Span, Context]]], Any]
    ) -> Callable[[Optional[Union[Span, Context]]], Any]:
        """Register a function to execute when a span is activated.

        Can be used as a decorator.

        :param func: The function to call when a span is activated.
                     The activated span will be passed as argument.
        """
        self._hooks.register(self.activate, func)
        return func

    def _deregister_on_activate(
        self, func: Callable[[Optional[Union[Span, Context]]], Any]
    ) -> Callable[[Optional[Union[Span, Context]]], Any]:
        """Unregister a function registered to execute when a span is activated.

        Can be used as a decorator.

        :param func: The function to stop calling when a span is activated.
        """

        self._hooks.deregister(self.activate, func)
        return func

    def __call__(self, *args: Any, **kwargs: Any) -> Optional[Union[Context, Span]]:
        """Method available for backward-compatibility. It proxies the call to
        ``self.active()`` and must not do anything more.
        """
        return self.active()


class DatadogContextMixin(object):
    """Mixin that provides active span updating suitable for synchronous
    and asynchronous executions.
    """

    def activate(self, ctx: Optional[Union[Context, Span]]) -> None:
        raise NotImplementedError

    def _update_active(self, span: Span) -> Optional[Span]:
        """Updates the active span in an executor.

        The active span is updated to be the span's parent if the span has
        finished until an unfinished span is found.
        """
        if span.finished:
            new_active: Optional[Span] = span
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

    def __init__(self) -> None:
        super(DefaultContextProvider, self).__init__()

    def _has_active_context(self) -> bool:
        """Returns whether there is an active context in the current execution."""
        ctx = _DD_CONTEXTVAR.get()
        return ctx is not None

    def activate(self, ctx: Optional[Union[Span, Context]]) -> None:
        """Makes the given context active in the current execution."""
        _DD_CONTEXTVAR.set(ctx)
        super(DefaultContextProvider, self).activate(ctx)

    def active(self) -> Optional[Union[Context, Span]]:
        """Returns the active span or context for the current execution."""
        item = _DD_CONTEXTVAR.get()
        if isinstance(item, Span):
            return self._update_active(item)
        return item
