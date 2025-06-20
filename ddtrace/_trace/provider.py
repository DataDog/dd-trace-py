import abc
import contextvars
from typing import Any
from typing import Optional
from typing import Union

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


ActiveTrace = Union[Span, Context]
_DD_CONTEXTVAR: contextvars.ContextVar[Optional[ActiveTrace]] = contextvars.ContextVar(
    "datadog_contextvar", default=None
)


class BaseContextProvider(metaclass=abc.ABCMeta):
    """
    A ``ContextProvider`` is an interface that provides the blueprint
    for a callable class, capable to retrieve the current active
    ``Context`` instance. Context providers must inherit this class
    and implement:
    * the ``active`` method, that returns the current active ``Context``
    * the ``activate`` method, that sets the current active ``Context``
    """

    @abc.abstractmethod
    def _has_active_context(self) -> bool:
        pass

    @abc.abstractmethod
    def activate(self, ctx: Optional[ActiveTrace]) -> None:
        core.dispatch("ddtrace.context_provider.activate", (ctx,))

    @abc.abstractmethod
    def active(self) -> Optional[ActiveTrace]:
        pass

    def __call__(self, *args: Any, **kwargs: Any) -> Optional[ActiveTrace]:
        """Method available for backward-compatibility. It proxies the call to
        ``self.active()`` and must not do anything more.
        """
        return self.active()


class DefaultContextProvider(BaseContextProvider):
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

    def activate(self, ctx: Optional[ActiveTrace]) -> None:
        """Makes the given context active in the current execution."""
        _DD_CONTEXTVAR.set(ctx)
        super(DefaultContextProvider, self).activate(ctx)

    def active(self) -> Optional[ActiveTrace]:
        """Returns the active span or context for the current execution."""
        item = _DD_CONTEXTVAR.get()
        if isinstance(item, Span):
            return self._update_active(item)
        return item

    def _update_active(self, span: Span) -> Optional[ActiveTrace]:
        """Updates the active trace in an executor.

        When a span finishes, the active span becomes its parent.
        If no parent exists and the context is reactivatable, that context is restored.
        """
        new_active: Optional[Span] = span
        # PERF: Avoid calling `Span.finished` more than once per span. This is a computed property.
        while new_active and new_active.finished:
            if new_active._parent is None and new_active._parent_context and new_active._parent_context._reactivate:
                self.activate(new_active._parent_context)
                return new_active._parent_context
            new_active = new_active._parent
        if new_active is not span:
            self.activate(new_active)
        return new_active
