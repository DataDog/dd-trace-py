"""
Type stubs for trace-specific events with concrete type annotations.
"""

from .span import Span
from ddtrace.internal.events import Event

# Trace events with concrete types
tracer_span_started: Event[[Span]]
tracer_span_finished: Event[[Span]]
