import os

from opentelemetry.context import _RuntimeContext
from opentelemetry.context import Context
from opentelemetry.trace.span import DEFAULT_TRACE_OPTIONS
from opentelemetry.trace.span import DEFAULT_TRACE_STATE

from ddtrace.context import Context as DDContext
from ddtrace.provider import DefaultContextProvider
from ddtrace.span import Span as DDSpan


os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"


class OtelRuntimeContext(_RuntimeContext, DefaultContextProvider):
    """The RuntimeContext interface provides a wrapper for the different
    mechanisms that are used to propagate context in Python.
    Implementations can be made available via entry_points and
    selected through environment variables.
    """

    def __init__(self):
        super(OtelRuntimeContext, self).__init__()

    def attach(self, context: Context) -> object:
        """Sets the current `Context` object. Returns a
        token that can be used to reset to the previous `Context`.
        Args:
            context: The Context to set.
        """
        # The API MUST return a value that can be used as a Token to restore the previous Context.
        if "trace_id" in context and "span_id" in context:
            dd_context = DDContext(context["trace_id"], context["span_id"])
            self.activate(dd_context)
        return None  # change this to a token

    def get_current(self) -> Context:
        """Returns the current `Context` object."""
        item = self.active()
        if isinstance(item, DDSpan) or isinstance(item, DDContext):
            return Context({"trace_id": item.trace_id, "span_id": item.span_id})
        return Context()

    def detach(self, token: object) -> None:
        """Resets Context to a previous value
        Args:
            token: A reference to a previous Context.
        """
        pass
        # self._current_context.reset(token)
        # attach needs to return a token before we can detach here
