"""Storage lifecycle of the selenium wrapping contexts.

No browser and no selenium import: the contexts are exercised over a plain function, which is all
that is needed to observe what they leave behind on the context variable.
"""

import pytest

from ddtrace.contrib.internal.selenium.patch import SeleniumWrappingContextBase
from ddtrace.internal.wrapping.context import _STORAGE_PREV


def _chain_depth(context) -> int:
    """How many per-call storage dicts the context still holds on its context variable."""
    depth = 0
    storage = context._storage.get()
    while storage is not None:
        depth += 1
        storage = storage.get(_STORAGE_PREV)
    return depth


class _RecordingContext(SeleniumWrappingContextBase):
    def __init__(self, f):
        super().__init__(f)
        self.returns = 0
        self.raise_on_return = False

    def _handle_return(self) -> None:
        self.returns += 1
        if self.raise_on_return:
            raise ValueError("instrumentation is broken")


def _target(value):
    if value == "boom":
        raise RuntimeError("boom")
    return value


@pytest.fixture
def context():
    ctx = _RecordingContext(_target)
    ctx.wrap()
    try:
        yield ctx
    finally:
        ctx.unwrap()


def test_a_successful_call_leaves_no_storage_behind(context):
    assert _target("ok") == "ok"

    assert context.returns == 1
    assert _chain_depth(context) == 0


def test_repeated_calls_do_not_accumulate_storage(context):
    for _ in range(50):
        _target("ok")

    # Regression: __return__ used to return the value without chaining to super(), so every
    # successful call chained another dict onto the context variable for the thread's lifetime.
    assert _chain_depth(context) == 0


def test_a_failing_call_leaves_no_storage_behind(context):
    with pytest.raises(RuntimeError):
        _target("boom")

    assert _chain_depth(context) == 0


def test_failing_instrumentation_neither_leaks_nor_reaches_the_caller(context):
    context.raise_on_return = True

    assert _target("ok") == "ok"
    assert _chain_depth(context) == 0
