"""
Event Hub - High-performance event system for ddtrace

This module provides a clean Python interface to the native event system,
allowing both Python and Rust code to register listeners and fire events
with minimal overhead.

Usage:
    from ddtrace.internal.events import trace_span_started

    # Register a listener
    def my_handler(span):
        print(f"Span started: {span}")

    handle = trace_span_started.on(my_handler)

    # Fire an event
    trace_span_started.dispatch(some_span)

    # Remove listener
    trace_span_started.remove(handle)

    # Check if any listeners exist
    if trace_span_started.has_listeners():
        trace_span_started.dispatch(span)
"""

from ddtrace.internal.native._native import events as _events


# Re-export everything from the native events submodule
__all__ = _events.__all__

# Dynamically add all event objects to this module's namespace
for name in _events.__all__:
    locals()[name] = getattr(_events, name)
