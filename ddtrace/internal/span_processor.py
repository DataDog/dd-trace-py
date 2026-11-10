import abc
from typing import Any
from typing import Generic
from typing import Optional
from typing import TypeVar

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData


log = get_logger(__name__)


# Contravariant so the tracer's own processors can narrow the span type to Span while product processors, which
# only rely on SpanData, remain usable anywhere a Span processor is expected.
SpanT = TypeVar("SpanT", bound=SpanData, contravariant=True)


class SpanProcessor(Generic[SpanT], metaclass=abc.ABCMeta):
    """A Processor is used to process spans as they are created and finished by a tracer.

    This base lives outside the tracing product so that other products can hook into span lifecycle events by
    registering a processor, without importing the tracer.
    """

    __processors__: list["SpanProcessor[Any]"] = []

    def __init__(self) -> None:
        """Default post initializer which logs the representation of the
        Processor at the ``logging.DEBUG`` level.
        """
        pass

    @abc.abstractmethod
    def on_span_start(self, span: SpanT) -> None:
        """Called when a span is started.

        This method is useful for making upfront decisions on spans.

        For example, a sampling decision can be made when the span is created
        based on its resource name.
        """
        pass

    @abc.abstractmethod
    def on_span_finish(self, span: SpanT) -> None:
        """Called with the result of any previous processors or initially with
        the finishing span when a span finishes.

        It can return any data which will be passed to any processors that are
        applied afterwards.
        """
        pass

    def shutdown(self, timeout: Optional[float]) -> None:
        """Called when the processor is done being used.

        Any clean-up or flushing should be performed with this method.
        """
        pass

    def register(self) -> None:
        """Register the processor with the global list of processors."""
        SpanProcessor.__processors__.append(self)

    def unregister(self) -> None:
        """Unregister the processor from the global list of processors."""
        try:
            SpanProcessor.__processors__.remove(self)
        except ValueError:
            log.warning("Span processor %r not registered", self)
