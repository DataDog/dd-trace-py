"""
Trace-specific events.

This module re-exports trace-related events from the native event system
with domain-specific type annotations.
"""

from ddtrace.internal import events


tracer_span_started = events.tracer_span_started
tracer_span_finished = events.tracer_span_finished

__all__ = [
    "tracer_span_started",
    "tracer_span_finished",
]
