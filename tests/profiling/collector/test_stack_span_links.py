"""Tests for profiler span-link lifecycle independent of execution integrations."""

from __future__ import annotations

import contextvars
import sys
import typing
from unittest.mock import Mock

import pytest

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.internal.datadog.profiling import stack
from ddtrace.profiling import _asyncio
from ddtrace.profiling import _span_links
from ddtrace.profiling.collector import stack as stack_collector


pytestmark = pytest.mark.skipif(not stack.is_available, reason="stack profiler not available")


@pytest.fixture(autouse=True)
def restore_span_linking_state():
    active = _span_links._span_linking_active
    generation = _span_links._span_link_generation
    active_span_link = _span_links._active_span_link.get()
    current_span_provider = _span_links._current_span_provider
    task_span_provider = _span_links._task_span_provider
    greenlet_span_provider = _span_links._greenlet_span_provider
    _span_links._span_linking_active = False
    _span_links._set_active_span_link(None)
    _span_links._current_span_provider = None
    _span_links._task_span_provider = None
    _span_links._greenlet_span_provider = None
    _span_links.stack.reset_span_links()
    try:
        yield
    finally:
        _span_links.stack.reset_span_links()
        _span_links._span_linking_active = active
        _span_links._span_link_generation = generation
        _span_links._set_active_span_link(active_span_link)
        _span_links._current_span_provider = current_span_provider
        _span_links._task_span_provider = task_span_provider
        _span_links._greenlet_span_provider = greenlet_span_provider


def _info(span_id: int, local_root_span_id: typing.Optional[int] = None) -> _span_links._SpanInfo:
    return _span_links._SpanInfo(span_id, local_root_span_id or span_id, None)


def test_start_republishes_current_span(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: linked.append(args))

    _span_links.start_span_linking(lambda: (_info(700), None))

    assert linked == [(700, 700, None)]


def test_context_deactivation_clears_physical_span_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear physical-thread attribution when the tracing context is deactivated."""
    cleared = []
    monkeypatch.setattr(_span_links.stack, "clear_span", lambda: cleared.append(True))

    _span_links.start_span_linking()
    _span_links.link_span(None, None)

    assert cleared == [True]


def test_span_activation_uses_task_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    monkeypatch.setattr(_span_links.stack, "link_task_span", lambda *args: linked.append(args))
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: pytest.fail("unexpected thread fallback"))

    _span_links.register_task_span_provider(lambda: 22)
    _span_links.start_span_linking()
    _span_links.link_span(_info(101), None)

    assert linked == [(22, 101, 101, None)]


def test_span_activation_uses_greenlet_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    monkeypatch.setattr(_span_links.stack, "link_greenlet_span", lambda *args: linked.append(args))
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: pytest.fail("unexpected thread fallback"))

    _span_links.register_greenlet_span_provider(lambda: 31)
    _span_links.start_span_linking()
    _span_links.link_span(_info(201), None)

    assert linked == [(31, 201, 201, None)]


def test_task_provider_takes_precedence_over_greenlet(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    monkeypatch.setattr(_span_links.stack, "link_task_span", lambda *args: linked.append(args))
    monkeypatch.setattr(
        _span_links.stack, "link_greenlet_span", lambda *args: pytest.fail("unexpected greenlet fallback")
    )

    _span_links.register_task_span_provider(lambda: 22)
    _span_links.register_greenlet_span_provider(lambda: 31)
    _span_links.start_span_linking()
    _span_links.link_span(_info(101), None)

    assert linked == [(22, 101, 101, None)]


def test_task_provider_failure_uses_greenlet(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    monkeypatch.setattr(_span_links.stack, "link_greenlet_span", lambda *args: linked.append(args))

    def broken_provider():
        raise RuntimeError("provider failed")

    _span_links.register_task_span_provider(broken_provider)
    _span_links.register_greenlet_span_provider(lambda: 31)
    _span_links.start_span_linking()
    _span_links.link_span(_info(202), None)

    assert linked == [(31, 202, 202, None)]


def test_task_provider_failure_uses_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: linked.append(args))

    def broken_provider():
        raise RuntimeError("provider failed")

    _span_links.register_task_span_provider(broken_provider)
    _span_links.start_span_linking()
    _span_links.link_span(_info(102), None)

    assert linked == [(102, 102, None)]


def test_task_detachment_does_not_clear_thread_link(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared = []
    monkeypatch.setattr(_span_links.stack, "clear_task_span", lambda task_id: cleared.append(task_id))
    monkeypatch.setattr(_span_links.stack, "clear_span", lambda: pytest.fail("unexpected thread clear"))

    _span_links.register_task_span_provider(lambda: 33)
    _span_links.start_span_linking()
    _span_links.link_span(None, None)

    assert cleared == [33]


def test_greenlet_detachment_does_not_clear_thread_link(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared = []
    monkeypatch.setattr(_span_links.stack, "clear_greenlet_span", lambda greenlet_id: cleared.append(greenlet_id))
    monkeypatch.setattr(_span_links.stack, "clear_span", lambda: pytest.fail("unexpected thread clear"))

    _span_links.register_greenlet_span_provider(lambda: 44)
    _span_links.start_span_linking()
    _span_links.link_span(None, None)

    assert cleared == [44]


def test_inherited_context_seeds_task_span_for_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    cleared = []
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: None)
    monkeypatch.setattr(_span_links.stack, "link_task_span", lambda *args: linked.append(args))
    monkeypatch.setattr(_span_links.stack, "clear_task_span", lambda task_id: cleared.append(task_id))

    _span_links.start_span_linking()
    _span_links.link_span(_info(701), None)
    inherited_context = contextvars.copy_context()
    assert _span_links.link_task_span_context(71, inherited_context)

    _span_links.stop_span_linking()
    _span_links.start_span_linking()
    assert not _span_links.link_task_span_context(72, inherited_context)

    assert linked == [(71, 701, 701, None)]
    assert cleared == [72]


def test_inherited_context_seeds_thread_span_for_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept inherited context only in the profiler lifecycle that created it."""
    linked = []
    cleared = []
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: linked.append(args))
    monkeypatch.setattr(_span_links.stack, "clear_span", lambda: cleared.append(True))

    _span_links.start_span_linking()
    _span_links.link_span(_info(701), None)
    inherited_context = contextvars.copy_context()
    inherited_context.run(_span_links.link_thread_span_context)

    _span_links.stop_span_linking()
    _span_links.start_span_linking()
    inherited_context.run(_span_links.link_thread_span_context)

    assert linked == [(701, 701, None), (701, 701, None)]
    assert cleared == [True]


def test_inherited_context_rejects_finished_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject inherited context after its source span has finished."""
    linked = []
    cleared = []
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: linked.append(args))
    monkeypatch.setattr(_span_links.stack, "clear_span", lambda: cleared.append(True))

    span = Span("test")
    _span_links.start_span_linking()
    _span_links.link_span(_info(span.span_id), span)
    inherited_context = contextvars.copy_context()
    span.finish()

    assert not inherited_context.run(_span_links.link_thread_span_context)
    assert linked == [(span.span_id, span.span_id, None)]
    assert cleared == [True]


def test_postfork_reset_invalidates_all_inherited_span_link_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalidate native mappings and copied ContextVar state after a fork reset."""
    resets = []
    monkeypatch.setattr(_span_links.stack, "reset_span_links", lambda: resets.append(True))

    _span_links.start_span_linking()
    _span_links.link_span(_info(701), None)
    generation = _span_links._span_link_generation
    _span_links._reset_span_link_state()

    assert resets == [True, True]
    assert _span_links._span_link_generation == generation + 1
    assert _span_links._active_span_link.get() is None


def test_collector_postfork_reset_restores_active_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore native loop registration before republishing the child's active span."""
    active = Context(trace_id=1, span_id=701)
    tracer = Mock()
    tracer.context_provider.active.return_value = active
    calls = []
    monkeypatch.setattr(_span_links, "_reset_span_link_state", lambda: calls.append(("reset", None)))
    monkeypatch.setattr(_asyncio, "link_existing_loop_to_current_thread", lambda: calls.append(("loop", None)))
    monkeypatch.setattr(_span_links, "link_span", lambda info, source: calls.append(("link", info)))

    stack_collector.StackCollector(tracer=tracer)._child_after_fork()

    assert calls == [("reset", None), ("loop", None), ("link", _info(701))]


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="safe ContextVar setter is only needed before Python 3.12")
def test_active_span_link_uses_safe_contextvar_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the crash-safe ContextVar setter on affected Python versions."""
    calls = []
    monkeypatch.setattr(_span_links, "safe_contextvar_set", lambda variable, value: calls.append((variable, value)))
    value = _span_links._SpanLinkContext(1, _span_links._SpanInfo(2, 3, "web"), None)

    _span_links._set_active_span_link(value)

    assert calls == [(_span_links._active_span_link, value)]


@pytest.mark.parametrize("target", ["thread", "task", "greenlet"])
@pytest.mark.parametrize("invalidation", ["finish", "restart", "stop"])
def test_invalidation_during_inherited_publication(monkeypatch, target, invalidation):
    source = Span("source")
    _span_links.start_span_linking()
    _span_links.link_span(_info(source.span_id), source)
    inherited = contextvars.copy_context()
    unlinked = []
    monkeypatch.setattr(_span_links.stack, "unlink_span", lambda span_id: unlinked.append(("thread", None, span_id)))
    monkeypatch.setattr(
        _span_links.stack, "unlink_task_span", lambda task, span_id: unlinked.append(("task", task, span_id))
    )
    monkeypatch.setattr(
        _span_links.stack,
        "unlink_greenlet_span",
        lambda greenlet, span_id: unlinked.append(("greenlet", greenlet, span_id)),
    )

    def invalidate_then_publish(info, task_id=None, greenlet_id=None):
        if invalidation == "finish":
            source.finish()
        else:
            _span_links.stop_span_linking()
            if invalidation == "restart":
                _span_links.start_span_linking()
        # The native write would now reintroduce metadata validated before the invalidation.

    monkeypatch.setattr(_span_links, "_publish_span", invalidate_then_publish)
    if target == "thread":
        assert not inherited.run(_span_links.link_thread_span_context)
    elif target == "task":
        assert not _span_links.link_task_span_context(22, inherited)
    else:
        assert not _span_links.link_greenlet_span_context(22, inherited)
    assert unlinked == [(target, None if target == "thread" else 22, source.span_id)]


def test_activation_rejects_span_finished_during_task_lookup(monkeypatch):
    source = Span("source")

    def task_provider():
        source.finish()
        return 22

    _span_links.start_span_linking()
    _span_links.register_task_span_provider(task_provider)
    monkeypatch.setattr(_span_links, "_publish_span", lambda *args: pytest.fail("published a finished span"))
    _span_links.link_span(_info(source.span_id), source)


@pytest.mark.parametrize("invalidation", ["finish", "restart", "stop"])
def test_current_greenlet_rejects_invalidation_during_context_lookup(monkeypatch, invalidation):
    source = Span("source")
    _span_links.start_span_linking()

    def current_span_provider():
        if invalidation == "finish":
            source.finish()
        else:
            _span_links.stop_span_linking()
            if invalidation == "restart":
                _span_links.start_span_linking()
        return _info(source.span_id), source

    monkeypatch.setattr(_span_links, "_current_span_provider", current_span_provider)
    monkeypatch.setattr(_span_links, "_publish_span", lambda *args: pytest.fail("published stale metadata"))
    assert not _span_links.link_current_greenlet_span(33)


@pytest.mark.parametrize("publication", ["current", "activation"])
@pytest.mark.parametrize("invalidation", ["finish", "restart", "stop"])
def test_greenlet_retracts_invalidation_during_publication(monkeypatch, publication, invalidation):
    source = Span("source")
    _span_links.start_span_linking()
    _span_links.register_greenlet_span_provider(lambda: 33)
    monkeypatch.setattr(_span_links, "_current_span_provider", lambda: (_info(source.span_id), source))
    unlinked = []
    monkeypatch.setattr(
        _span_links.stack, "unlink_greenlet_span", lambda greenlet, span_id: unlinked.append((greenlet, span_id))
    )

    def invalidate_then_publish(info, task_id=None, greenlet_id=None):
        if invalidation == "finish":
            source.finish()
        else:
            _span_links.stop_span_linking()
            if invalidation == "restart":
                _span_links.start_span_linking()

    monkeypatch.setattr(_span_links, "_publish_span", invalidate_then_publish)
    if publication == "current":
        assert not _span_links.link_current_greenlet_span(33)
    else:
        _span_links.link_span(_info(source.span_id), source)
    assert unlinked == [(33, source.span_id)]
