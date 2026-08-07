"""Shared utilities for profiling collector tests."""

import asyncio
import os
from types import TracebackType
from typing import Any
from typing import Coroutine
from typing import Optional
from typing import TypeVar

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import profiler


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

        return uvloop.run(coro)  # type: ignore[no-any-return]
    else:
        return asyncio.run(coro)


def uvloop_available() -> bool:
    try:
        import uvloop  # noqa: F401

        return True
    except ImportError:
        return False


def wait_for_fast_copy_upgrade(stack: Any, timeout: float = 10.0, *, require_warmup: bool = True) -> tuple[bool, bool]:
    """Wait until warmup (syscall copy) then upgrade to safe_memcpy.

    Returns (saw_warmup, saw_upgrade). When process_vm_readv is unavailable the
    sampler may skip warmup and stay on safe_memcpy (saw_warmup=False).
    """
    import time

    saw_warmup = False
    saw_upgrade = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = stack.fast_copy_memory_active()
        if not saw_warmup:
            if active is False:
                saw_warmup = True
        elif active is True:
            saw_upgrade = True
            break
        time.sleep(0.05)

    # When process_vm_readv is unavailable the sampler skips warmup and stays on safe_memcpy.
    if not saw_upgrade and stack.fast_copy_memory_active():
        saw_upgrade = True

    if require_warmup:
        assert saw_warmup, "Expected the sampler to run on the syscall copy during the warmup window"
    assert saw_upgrade, "Expected the sampler to upgrade to safe_memcpy after warmup"
    return saw_warmup, saw_upgrade


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
