import abc
from typing import Any
from typing import List
from typing import Optional

from ddtrace import Span
from ddtrace.filters import TraceFilter
from ddtrace.vendor import attr
from ddtrace.vendor import six

from .logger import get_logger


log = get_logger(__name__)


@attr.s
class Processor(six.with_metaclass(abc.ABCMeta)):
    """A Processor is used to process spans as they are created and finished by a tracer."""

    def __attrs_post_init__(self):
        # type () -> None
        """Default post initializer which logs the representation of the
        Processor at the ``logging.DEBUG`` level.

        The representation can be modified with the ``repr`` argument to the
        attrs attribute::

            @attr.s
            class MyProcessor(Processor):
                field_to_include = attr.ib(repr=True)
                field_to_exclude = attr.ib(repr=False)
        """
        log.debug("initialized processor %r", self)

    @abc.abstractmethod
    def on_span_start(self, span):
        # type: (Span) -> None
        """Called when a span is started.

        This method is useful for making upfront decisions on spans.

        For example, a sampling decision can be made when the span is created
        based on its resource name.
        """
        pass

    @abc.abstractmethod
    def on_span_finish(self, data):
        # type: (Any) -> Any
        """Called with the result of any previous processors or initially with
        the finishing span when a span finishes.

        It can return any data which will be passed to any processors that are
        applied afterwards.
        """
        pass


@attr.s(eq=False)
class TraceFilterProcessor(Processor):
    """Processor for applying a ``TraceFilter`` to traces.

    If a filter raises an exception it will be logged at the ``logging.ERROR``
    level and the trace will be returned.
    """

    _filter = attr.ib(type=TraceFilter, repr=True)

    def on_span_start(self, span):
        # type: (Span) -> None
        return None

    def on_span_finish(self, trace):
        # type: (Optional[List[Span]]) -> Optional[List[Span]]
        if not trace:
            return trace

        trace_id = trace[0].trace_id
        try:
            log.debug("applying filter %r to %d", self._filter, trace_id)
            trace = self._filter.process_trace(trace)
        except Exception:
            log.error("error applying filter %r to traces", self._filter, exc_info=True)
        else:
            if trace is None:
                log.debug("trace %d dropped due to filter %r", trace_id, self._filter)
                return None

        return trace
