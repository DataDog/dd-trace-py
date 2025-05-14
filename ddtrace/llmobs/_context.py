import contextvars
from typing import Optional
from typing import Union

from ddtrace._trace.context import Context
from ddtrace._trace.provider import DefaultContextProvider
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes


ContextTypeValue = Optional[Union[Context, Span]]


_DD_LLMOBS_CONTEXTVAR: contextvars.ContextVar[ContextTypeValue] = contextvars.ContextVar(
    "datadog_llmobs_contextvar",
    default=None,
)


class LLMObsContextProvider(DefaultContextProvider):
    """Context provider that retrieves contexts from a context variable.
    It is suitable for synchronous programming and for asynchronous executors
    that support contextvars.
    """
    _LAST_PROPAGATED_LLMOBS_CONTEXT: Optional[Context] = None

    def __init__(self) -> None:
        super(DefaultContextProvider, self).__init__()
        _DD_LLMOBS_CONTEXTVAR.set(None)

    def _has_active_context(self) -> bool:
        """Returns whether there is an active context in the current execution."""
        ctx = _DD_LLMOBS_CONTEXTVAR.get()
        return ctx is not None

    def _update_active(self, span: Span) -> Optional[Span]:
        """Updates the active LLMObs span.
        The active span is updated to be the span's closest unfinished LLMObs ancestor span.
        """
        if not span.finished:
            return span
        new_active: Optional[Span] = span._parent
        while new_active:
            if not new_active.finished and new_active.span_type == SpanTypes.LLM:
                self.activate(new_active)
                return new_active
            new_active = new_active._parent
        if self._LAST_PROPAGATED_LLMOBS_CONTEXT:
            return self._LAST_PROPAGATED_LLMOBS_CONTEXT
        self.activate(None)
        return None

    def activate(self, ctx: ContextTypeValue) -> None:
        """Makes the given context active in the current execution."""
        _DD_LLMOBS_CONTEXTVAR.set(ctx)
        super(DefaultContextProvider, self).activate(ctx)

    def active(self) -> ContextTypeValue:
        """Returns the active span or context for the current execution."""
        item = _DD_LLMOBS_CONTEXTVAR.get()
        if isinstance(item, Span):
            return self._update_active(item)
        return item
