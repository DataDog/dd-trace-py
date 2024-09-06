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
    The underlying implementation for ContextVar involves an immutable data structure (HAMT), the maintenance of which
    may require de-referecing pointers to Python objects interned in the Context().  Sometimes the lifetimes of those
    objects are mismanaged, causing them to be GC'd even though the Context() is supposed to hold a strong reference.

    That situation can cause segmentation faults to originate from this code (even though it's not our fault). In
    order to get around this, we create a wrapper object that holds a strong reference to the object we want to,
    which allows us to `set()` the contextvar only once.  Since the issue mainly arises during re-allocation (i.e.,
    when a key is associated to a new value), this technique should minimize the clone operations arising from
    this code.
    """

    def __init__(self, name: str):
        self._name = name
        self._context_var = contextvars.ContextVar[Optional[Union[Context, Span]]](name, default=None)

    def get(self) -> Optional[Union[Context, Span]]:
        """Retrieve the current context or span from the wrapper."""
        if self._context_var is None:
            return None
        return self._context_var.get()

    def set(self, value: Optional[Union[Context, Span]]) -> None:
        """
        Set the context variable, ensuring the value is wrapped appropriately.
        """
        if self._context_var is None:
            self._context_var = contextvars.ContextVar[Optional[Union[Context, Span]]](self._name, default=value)
        else:
            self._context_var.set(value)


# Initialize the context manager
_DD_CONTEXTVAR = ContextVarManager("datadog_contextvar")


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
