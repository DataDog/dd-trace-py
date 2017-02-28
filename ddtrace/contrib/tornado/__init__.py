"""
TODO: how to use Tornado instrumentation
"""
from ..util import require_modules


required_modules = ['tornado']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .stack_context import run_with_trace_context, TracerStackContext
        from .middlewares import TraceMiddleware

        # alias for API compatibility
        context_provider = TracerStackContext.current_context

        __all__ = [
            'context_provider',
            'run_with_trace_context',
            'TraceMiddleware',
            'TracerStackContext',
        ]
