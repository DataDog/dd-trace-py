import contextvars
import typing as t

from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.internal.native._native import Context
from ddtrace.internal.native._native import SpanData


ContextTypeValue = t.Optional[t.Union[Context, SpanData]]


_DD_CI_CONTEXTVAR: contextvars.ContextVar[ContextTypeValue] = contextvars.ContextVar(
    "datadog_civisibility_contextvar",
    default=None,
)


class CIContextProvider(DefaultContextProvider):
    """Context provider that retrieves contexts from a context variable.

    It is suitable for synchronous programming and for asynchronous executors
    that support contextvars.
    """

    def __init__(self):
        # type: () -> None
        super(DefaultContextProvider, self).__init__()
        _DD_CI_CONTEXTVAR.set(None)

    def _has_active_context(self):
        # type: () -> bool
        """Returns whether there is an active context in the current execution."""
        ctx = _DD_CI_CONTEXTVAR.get()
        return ctx is not None

    def activate(self, ctx: ContextTypeValue) -> None:
        """Makes the given context active in the current execution."""
        _DD_CI_CONTEXTVAR.set(ctx)
        super(DefaultContextProvider, self).activate(t.cast(t.Any, ctx))

    def active(self) -> t.Optional[t.Any]:
        """Returns the active span or context for the current execution."""
        item = _DD_CI_CONTEXTVAR.get()
        if isinstance(item, SpanData):
            return self._update_active(t.cast(t.Any, item))
        return item
