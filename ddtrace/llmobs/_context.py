import contextvars
from typing import Any
from typing import Optional
from typing import Union
from typing import cast

from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.ext import SpanTypes
from ddtrace.internal.native._native import Context
from ddtrace.internal.native._native import SpanData


ContextTypeValue = Optional[Union[Context, SpanData]]


_DD_LLMOBS_CONTEXTVAR: contextvars.ContextVar[ContextTypeValue] = contextvars.ContextVar(
    "datadog_llmobs_contextvar",
    default=None,
)


class LLMObsContextProvider(DefaultContextProvider):
    """Context provider that retrieves contexts from a context variable.
    It is suitable for synchronous programming and for asynchronous executors
    that support contextvars.
    """

    def __init__(self) -> None:
        super(DefaultContextProvider, self).__init__()
        _DD_LLMOBS_CONTEXTVAR.set(None)

    def _has_active_context(self) -> bool:
        """Returns whether there is an active context in the current execution."""
        ctx = _DD_LLMOBS_CONTEXTVAR.get()
        return ctx is not None

    def _update_active(self, span: SpanData) -> Optional[Any]:
        """Updates the active LLMObs span.
        The active span is updated to be the span's closest unfinished LLMObs ancestor span.
        """
        if not span.finished:
            return span
        new_active: Optional[SpanData] = span._parent
        while new_active:
            if not new_active.finished and new_active.span_type == SpanTypes.LLM:
                self.activate(new_active)
                return new_active
            new_active = new_active._parent
        self.activate(None)
        return None

    def activate(self, ctx: ContextTypeValue) -> None:
        """Makes the given context active in the current execution."""
        _DD_LLMOBS_CONTEXTVAR.set(ctx)
        super(DefaultContextProvider, self).activate(cast(Any, ctx))

    def active(self) -> Optional[Any]:
        """Returns the active span or context for the current execution."""
        item = _DD_LLMOBS_CONTEXTVAR.get()
        if isinstance(item, SpanData):
            return self._update_active(item)
        return item
