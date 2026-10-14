"""
dd-trace-py interaction layer.
"""

import typing as t

from ddtrace_base.codeowners import Codeowners  # noqa: F401
from ddtrace_base.utils.time import StopWatch  # noqa: F401
from ddtrace_base.utils.time import Time  # noqa: F401


def _pure_msgpack_packb(*args: t.Any, **kwargs: t.Any) -> bytes:
    """Standalone encoder: msgpack.packb with the flags ddtrace's native encoder uses."""
    import msgpack

    kwargs.setdefault("use_bin_type", True)
    return t.cast("bytes", msgpack.packb(*args, **kwargs))


def __getattr__(name: str) -> t.Any:
    # Lazy re-export: rand64bits lives in the native extension; importing it
    # eagerly would load the extension at plugin import time.
    if name == "rand64bits":
        from ddtrace.internal.native import rand64bits

        return rand64bits
    if name == "msgpack_packb":
        # The native encoder produces the same bytes as msgpack.packb for our
        # payloads, only faster; when ddtrace is not installed we fall back to it.
        try:
            from ddtrace.internal._encoding import packb
        except ImportError:
            packb = _pure_msgpack_packb
        return packb
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
