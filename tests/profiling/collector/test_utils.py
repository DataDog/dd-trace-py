"""Shared utilities for profiling collector tests."""

import asyncio
import os
from typing import Any
from typing import Coroutine
from typing import TypeVar

from ddtrace.internal.datadog.profiling import ddup


T = TypeVar("T")


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


def async_run(coro: Coroutine[Any, Any, T]) -> T:
    use_uvloop = os.environ.get("USE_UVLOOP", "0") == "1"

    if use_uvloop:
        import uvloop

        return uvloop.run(coro)
    else:
        return asyncio.run(coro)
