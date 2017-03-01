"""
TODO: how to use Tornado instrumentation
"""
from ..util import require_modules


required_modules = ['tornado']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .tracer import TornadoTracer
        from .middlewares import TraceMiddleware
        from .stack_context import ContextManager

        # a global Tornado tracer instance
        tracer = TornadoTracer()

        __all__ = [
            'tracer',
            'ContextManager',
            'TraceMiddleware',
        ]
