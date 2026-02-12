"""Shared utilities for profiling collector tests."""

import asyncio
import os
from types import TracebackType
from typing import Any
from typing import Coroutine
from typing import Optional

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import profiler


def init_ddup(test_name: str) -> None:
    """Initialize ddup for profiling tests.

    Must be called before using any lock collectors.

    Args:
        test_name: Name of the test, used for service name and output filename.
    """
    assert ddup.is_available, "ddup is not available"
    ddup.config(
        env="test",
        service=test_name,
        version="1.0",
        output_filename="/tmp/" + test_name,
    )
    ddup.start()


def async_run(coro: Coroutine[Any, Any, Any]) -> None:
    use_uvloop = os.environ.get("USE_UVLOOP", "0") == "1"

    if use_uvloop:
        import uvloop

        uvloop.run(coro)
    else:
        asyncio.run(coro)


def uvloop_available() -> bool:
    try:
        import uvloop  # noqa: F401

        return True
    except ImportError:
        return False


class ProfilerContextManager:
    def __init__(self) -> None:
        self.profiler = profiler.Profiler()

    def __enter__(self) -> profiler.Profiler:
        self.profiler.start()
        return self.profiler

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.profiler.stop()
