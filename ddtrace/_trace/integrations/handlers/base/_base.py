"""
Base handler class for span event processing.

SpanEventHandler provides the core span lifecycle management via context managers.
Subclasses override hooks for type-specific behavior (DBM, DSM, peer service, etc.).
"""

from contextlib import asynccontextmanager
from contextlib import contextmanager
import sys
from typing import TYPE_CHECKING
from typing import Generator
from typing import Generic
from typing import TypeVar

from ddtrace import tracer
from ddtrace._trace.integrations.events.base._base import SpanEvent


if TYPE_CHECKING:
    from ddtrace._trace.span import Span


# TypeVar bound to SpanEvent allows subclasses to specify their event type
E = TypeVar("E", bound=SpanEvent)


class SpanEventHandler(Generic[E]):
    """
    Base handler for all span events.

    Provides the core span lifecycle management via context managers.
    Subclasses override hooks for type-specific behavior.
    """

    @contextmanager
    def trace(self, event: E) -> Generator["Span", None, None]:
        """
        Context manager that handles full span lifecycle.

        Usage:
            handler = get_handler(event)
            with handler.trace(event) as span:
                result = do_work()
                event.db_row_count = len(result)
            # Span automatically finished
        """
        span = self.create_span(event)
        try:
            yield span
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self.on_finish(span, event)
            span.finish()

    @asynccontextmanager
    async def trace_async(self, event: E):
        """Async version of trace() for async integrations."""
        span = self.create_span(event)
        try:
            yield span
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self.on_finish(span, event)
            span.finish()

    def create_span(self, event: E) -> "Span":
        """
        Create span with base attributes and tags.

        Override in subclasses to add type-specific setup.
        """
        span = tracer.trace(
            event._span_name,
            service=event._service,
            resource=event._resource,
            span_type=event._span_type,
        )
        self.apply_tags(span, event)
        return span

    def apply_tags(self, span: "Span", event: E) -> None:
        """Apply all public fields from event as span tags."""
        for field_name, value in event.get_tags().items():
            if value is not None:
                if isinstance(value, (int, float)):
                    span.set_metric(field_name, value)
                else:
                    span._set_tag_str(field_name, str(value))

    def on_finish(self, span: "Span", event: E) -> None:
        """
        Hook called before span.finish().

        Override in subclasses for type-specific cleanup.
        Default implementation re-applies tags to capture updates.
        """
        self.apply_tags(span, event)
