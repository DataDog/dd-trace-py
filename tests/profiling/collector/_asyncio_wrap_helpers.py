"""Subprocess-body helpers for the asyncio-wrap tests.

``@pytest.mark.subprocess`` extracts only the test function's body and
executes it as a standalone script (see ``FunctionDefFinder`` in
``tests/conftest.py``).  Module-level definitions in the test file are
therefore invisible to the subprocess.  Helpers must live in a separate
importable module — which this file is.

Imported as ``from tests.profiling.collector._asyncio_wrap_helpers import ...``
inside each subprocess test body.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from typing import Callable
from typing import Iterator


@contextmanager
def started_profiler() -> Iterator[Any]:
    """Context manager that starts the profiler on entry and stops it on
    exit, even on assertion failure. Yields the Profiler instance.
    """
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    try:
        yield p
    finally:
        p.stop()


@contextmanager
def captured_link_calls(attr: str) -> Iterator[list[int]]:
    """Replace ``ddtrace.internal.datadog.profiling.stack.<attr>`` with a
    recorder that captures the child task id of each call, yielding the
    growing list.  Restores the original on exit.

    ``attr`` is one of ``"link_tasks"`` / ``"weak_link_tasks"`` /
    ``"track_asyncio_loop"`` — anything with a ``(_, second_arg)`` shape
    where the second arg is the thing we want to identify by id.

    Raises ``AssertionError`` (with ``stack.failure_msg``) if the native
    stack extension is unavailable — surfaces the root cause clearly
    rather than an opaque ``AttributeError`` on ``getattr(stack, attr)``.
    """
    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available, stack.failure_msg

    original: Callable[..., Any] = getattr(stack, attr)
    recorded: list[int] = []

    def recorder(first: Any, second: Any) -> Any:
        recorded.append(id(second))
        return original(first, second)

    setattr(stack, attr, recorder)
    try:
        yield recorded
    finally:
        setattr(stack, attr, original)
