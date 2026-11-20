"""Helpers for test_asyncio_task_lists.py.

Importing this module imports nothing from ddtrace, so tests can arrange sys.modules first. Importing ddtrace itself
imports asyncio.
"""

import logging
import sys
from types import ModuleType
from typing import Any
from typing import Optional


NO_TASK_LISTS_MESSAGE = "Could not locate the native asyncio task lists"


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def capture_asyncio_log() -> list[str]:
    """Collect the messages ddtrace.profiling._asyncio logs. Call before importing ddtrace."""
    logger = logging.getLogger("ddtrace.profiling._asyncio")
    logger.setLevel(logging.DEBUG)
    handler = _ListHandler()
    logger.addHandler(handler)
    return handler.messages


def no_task_lists_logs(messages: list[str]) -> int:
    return sum(NO_TASK_LISTS_MESSAGE in message for message in messages)


def purge_asyncio() -> None:
    """Forget asyncio and _asyncio so the next import executes them again."""
    for name in list(sys.modules):
        if name in ("asyncio", "_asyncio") or name.startswith("asyncio."):
            del sys.modules[name]


def record_init_asyncio(result: Optional[bool] = None) -> list[tuple[Optional[ModuleType], bool]]:
    """Record each stack.init_asyncio call as (module passed, value returned).

    _asyncio.py looks init_asyncio up on the stack package at call time, so replacing it there catches every call. The
    real function still runs. When result is set, the wrapper returns it instead of the real value.
    """
    from ddtrace.internal.datadog.profiling import stack

    calls: list[tuple[Optional[ModuleType], bool]] = []
    real = stack.init_asyncio

    def init_asyncio(scheduled: Any, eager: Any, asyncio_c: Optional[ModuleType] = None) -> bool:
        value = real(scheduled, eager, asyncio_c)
        if result is not None:
            value = result
        calls.append((asyncio_c, value))
        return value

    stack.init_asyncio = init_asyncio  # type: ignore[assignment]
    return calls
