"""
dd-trace-py interaction layer.
"""

from ddtrace.internal._encoding import packb as msgpack_packb  # noqa: F401
from ddtrace.internal.base.utils.time import StopWatch  # noqa: F401
from ddtrace.internal.base.utils.time import Time  # noqa: F401
from ddtrace.internal.codeowners import Codeowners  # noqa: F401


def __getattr__(name: str):
    # Lazy re-export: rand64bits lives in the native extension; importing it
    # eagerly would load the extension at plugin import time.
    if name == "rand64bits":
        from ddtrace.internal.native import rand64bits

        return rand64bits
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
