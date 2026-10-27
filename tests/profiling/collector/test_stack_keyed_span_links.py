"""Tests for the keyed span link bindings of the stack extension.

Nothing in the profiler calls these bindings yet. The tests check the Python-facing contract that later changes rely
on: the lifecycle token rules, the per-domain keys, tombstones and birth, and argument handling.
"""

from collections.abc import Iterator
import threading
from typing import Optional

import pytest

from ddtrace.internal.datadog.profiling import stack


pytestmark = pytest.mark.skipif(not stack.is_available, reason="stack profiler not available")

if stack.is_available:
    from ddtrace.internal.datadog.profiling.stack import _stack

    THREAD = _stack.SPAN_LINK_DOMAIN_THREAD
    TASK = _stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK
    GREENLET = _stack.SPAN_LINK_DOMAIN_GREENLET
    SPAN = _stack.SPAN_LINK_KIND_SPAN
    CONTEXT = _stack.SPAN_LINK_KIND_CONTEXT


@pytest.fixture
def token() -> Iterator[int]:
    stack.links_close()
    stack.links_open()
    yield stack.lifecycle()
    stack.links_close()


def entry(domain: int, identifier: int) -> Optional[tuple["_stack.SpanLinkEntry", int]]:
    return _stack._span_link_entry(domain, identifier)


def span_of(domain: int, identifier: int) -> "_stack.SpanLinkEntry":
    found = entry(domain, identifier)
    return None if found is None else found[0]


def birth_of(domain: int, identifier: int) -> int:
    found = entry(domain, identifier)
    assert found is not None
    return found[1]


def test_lifecycle_token_is_odd_only_while_open() -> None:
    stack.links_close()
    closed = stack.lifecycle()
    assert closed % 2 == 0

    stack.links_open()
    opened = stack.lifecycle()
    assert opened % 2 == 1
    assert opened > closed

    stack.links_close()
    assert stack.lifecycle() == opened + 1


def test_link_requires_current_open_token(token: int) -> None:
    stack.link(token + 2, False, 1, 0, (10, 1, "web", SPAN))
    assert entry(TASK, 1) is None

    stack.link(token, False, 1, 0, (10, 1, "web", SPAN))
    assert span_of(TASK, 1) == (10, 1, "web", SPAN)

    stack.links_close()
    assert entry(TASK, 1) is None
    stack.link(token, False, 1, 0, (10, 1, "web", SPAN))
    stack.link(stack.lifecycle(), False, 1, 0, (10, 1, "web", SPAN))
    assert entry(TASK, 1) is None

    stack.links_open()
    stack.link(token, False, 1, 0, (10, 1, "web", SPAN))
    stack.retarget(token, 10, None)
    assert entry(TASK, 1) is None


def test_thread_key_is_the_calling_thread(token: int) -> None:
    stack.link(token, True, 0, 0, (10, 1, "web", SPAN))

    linked = threading.Event()
    done = threading.Event()

    def other() -> None:
        stack.link(token, True, 0, 0, (20, 2, None, CONTEXT))
        linked.set()
        # The thread key goes away when the thread exits, so keep the thread alive while the test reads it.
        done.wait()

    t = threading.Thread(target=other)
    t.start()
    try:
        assert linked.wait(timeout=10)
        assert entry(THREAD, threading.get_ident()) == ((10, 1, "web", SPAN), 0)
        # A missing span type is stored as an empty string, as link_span does.
        assert t.ident is not None
        assert span_of(THREAD, t.ident) == (20, 2, "", CONTEXT)
    finally:
        done.set()
        t.join()


def test_domains_are_separate(token: int) -> None:
    stack.link(token, False, 42, 0, (1, 1, "web", SPAN))
    stack.link(token, False, 0, 42, (2, 1, "web", SPAN))

    assert span_of(TASK, 42) == (1, 1, "web", SPAN)
    assert span_of(GREENLET, 42) == (2, 1, "web", SPAN)
    assert entry(THREAD, 42) is None

    stack.clear_task(42)
    assert entry(TASK, 42) is None
    assert span_of(GREENLET, 42) == (2, 1, "web", SPAN)

    stack.clear_greenlet(42)
    assert entry(GREENLET, 42) is None
    assert not stack.holds_span(1)
    assert not stack.holds_span(2)


def test_no_span_leaves_tombstones_on_logical_keys(token: int) -> None:
    stack.link(token, True, 1, 2, None)
    assert entry(TASK, 1) is None
    assert entry(GREENLET, 2) is None

    stack.link(token, True, 1, 2, (10, 1, "web", SPAN))
    task_birth = birth_of(TASK, 1)
    greenlet_birth = birth_of(GREENLET, 2)
    assert task_birth > 0
    assert greenlet_birth > task_birth

    stack.link(token, True, 1, 2, None)
    assert entry(THREAD, threading.get_ident()) is None
    assert entry(TASK, 1) == (None, task_birth)
    assert entry(GREENLET, 2) == (None, greenlet_birth)

    stack.link(token, False, 1, 0, (11, 1, "web", SPAN))
    assert entry(TASK, 1) == ((11, 1, "web", SPAN), task_birth)


def test_retarget_moves_only_span_links(token: int) -> None:
    stack.link(token, True, 1, 0, (10, 1, "web", SPAN))
    stack.link(token, False, 2, 0, (10, 1, "web", CONTEXT))
    assert stack.holds_span(10)

    stack.retarget(token, 10, (5, 1, "web", SPAN))
    assert span_of(THREAD, threading.get_ident()) == (5, 1, "web", SPAN)
    assert span_of(TASK, 1) == (5, 1, "web", SPAN)
    assert span_of(TASK, 2) == (10, 1, "web", CONTEXT)
    assert not stack.holds_span(10)
    assert stack.holds_span(5)

    stack.retarget(token, 5, None)
    assert entry(THREAD, threading.get_ident()) is None
    assert span_of(TASK, 1) is None
    assert entry(TASK, 1) is not None
    assert not stack.holds_span(5)


@pytest.mark.parametrize(
    "bad_entry, error",
    [
        ([10, 1, "web", 0], TypeError),
        ((10, 1, "web"), TypeError),
        ((10, 1, "web", 7), ValueError),
        (("x", 1, "web", 0), TypeError),
    ],
)
def test_link_rejects_malformed_entries(token: int, bad_entry: object, error: type) -> None:
    with pytest.raises(error):
        stack.link(token, False, 1, 0, bad_entry)  # type: ignore[arg-type]
    with pytest.raises(error):
        stack.retarget(token, 10, bad_entry)  # type: ignore[arg-type]
    assert entry(TASK, 1) is None


def test_link_span_is_unchanged_by_the_lifecycle() -> None:
    # The thread-only binding keeps working whether keyed links are open or not.
    stack.links_close()
    stack.link_span(10, 1, "web")
    try:
        assert span_of(THREAD, threading.get_ident()) == (10, 1, "web", SPAN)
        stack.unlink_finished_span(10)
        assert entry(THREAD, threading.get_ident()) is None
    finally:
        stack.clear_span()


# err=None: Python 3.12+ warns about fork() in a multi-threaded process.
@pytest.mark.subprocess(err=None)
def test_fork_child_keeps_the_token() -> None:
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack

    task = _stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK
    linked = (10, 1, "web", _stack.SPAN_LINK_KIND_SPAN)

    stack.register_fork_cleanup()
    stack.links_open()
    token = stack.lifecycle()
    stack.link(token, False, 1, 0, linked)
    before = _stack._span_link_entry(task, 1)
    assert before is not None

    pid = os.fork()
    if pid == 0:
        # The child starts with no keys, but keeps the token and a birth counter that only moves forward.
        ok = stack.lifecycle() == token and _stack._span_link_entry(task, 1) is None
        stack.link(token, False, 1, 0, linked)
        after = _stack._span_link_entry(task, 1)
        ok = ok and after is not None and after[1] > before[1]
        os._exit(0 if ok else 1)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
