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
class TraceFiltersProcessor(Processor):
    """Processor for applying a list of ``TraceFilter`` to traces.

    The filters are applied sequentially with the result of one passed on to
    the next. If ``None`` is returned by a filter then execution short-circuits
    and ``None`` is returned.

    If a filter raises an exception it will be logged at the ``logging.ERROR``
    level and the filter chain will continue with the next filter in the list.
    """

    _filters = attr.ib(type=List[TraceFilter], repr=True)

    def on_span_start(self, span):
        # type: (Span) -> None
        return None

    def on_span_finish(self, trace):
        # type: (Optional[List[Span]]) -> Optional[List[Span]]
        if trace is None:
            return

        for filtr in self._filters:
            try:
                log.debug("applying filter %r to %s", filtr, trace[0].trace_id if trace else [])
                trace = filtr.process_trace(trace)
            except Exception:
                log.error("error applying filter %r to traces", filtr, exc_info=True)
            else:
                if trace is None:
                    log.debug("dropping trace due to filter %r", filtr)
                    return

        return trace
