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
    _span_links._span_linking_active = False
    _span_links._set_active_span_link(None)
    _span_links._current_span_provider = None
    _span_links.stack.reset_span_links()
    try:
        yield
    finally:
        _span_links.stack.reset_span_links()
        _span_links._span_linking_active = active
        _span_links._span_link_generation = generation
        _span_links._set_active_span_link(active_span_link)
        _span_links._current_span_provider = current_span_provider


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
