"""
asyncpg instrumentation.

Ultra-minimal instrumentor that wraps asyncpg methods and emits events
with raw (instance, args, kwargs). All extraction and tracing is handled
by the asyncpg plugin in ddtrace._trace.tracing_plugins.contrib.asyncpg.
"""

from ddtrace.contrib.auto._base import Instrumentor


class AsyncpgInstrumentor(Instrumentor):
    """asyncpg instrumentation."""

    package = "asyncpg"

    supported_versions = {"asyncpg": ">=0.23.0"}

    methods_to_wrap = [
        ("protocol.Protocol.execute", "execute"),
        ("protocol.Protocol.bind_execute", "execute"),
        ("protocol.Protocol.query", "execute"),
        ("protocol.Protocol.bind_execute_many", "execute"),
    ]

    default_config = {
        "_default_service": "postgres",
    }

    def _make_wrapper(self, operation):
        # Override to use async wrapper for asyncpg
        return self._make_async_wrapper(operation)


# Module-level API
_instrumentor = AsyncpgInstrumentor()


def patch():
    _instrumentor.instrument()


def unpatch():
    _instrumentor.uninstrument()


def get_versions():
    return _instrumentor.supported_versions
