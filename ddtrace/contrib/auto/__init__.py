"""
Auto instrumentation package.

This package contains the new instrumentation API v2 - ultra-minimal
instrumentors that only wrap methods and emit events with raw objects.

All extraction and tracing logic is handled by plugins in
ddtrace._trace.tracing_plugins.
"""

from ddtrace.contrib.auto._base import Instrumentor


__all__ = ["Instrumentor"]
