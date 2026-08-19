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
    enabled = _span_links._span_linking_enabled
    generation = _span_links._span_link_generation
    active_span_link = _span_links._active_span_link.get()
    providers = list(_span_links._logical_span_providers)
    _span_links._span_linking_enabled = False
    _span_links._set_active_span_link(None)
    _span_links._logical_span_providers.clear()
    _span_links.stack.reset_span_links()
    try:
        yield
    finally:
        _span_links.stack.reset_span_links()
        _span_links._span_linking_enabled = enabled
        _span_links._span_link_generation = generation
        _span_links._set_active_span_link(active_span_link)
        _span_links._logical_span_providers[:] = providers


def _target(domain: _span_links.SpanLinkDomain, identifier: int) -> _span_links.LogicalSpanTarget:
    return _span_links.LogicalSpanTarget(domain, identifier)


def _info(span_id: int, local_root_span_id: typing.Optional[int] = None) -> _span_links._SpanInfo:
    return _span_links._SpanInfo(span_id, local_root_span_id or span_id, None)


def test_context_deactivation_clears_physical_span_link(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared = []
    monkeypatch.setattr(_span_links.stack, "clear_span", lambda: cleared.append(True))

    _span_links.enable_span_linking()
    _span_links.link_span(None, None)

    assert cleared == [True]


def test_span_activation_uses_highest_priority_logical_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    monkeypatch.setattr(_span_links.stack, "link_logical_span", lambda *args: linked.append(args))
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: pytest.fail("unexpected thread fallback"))

    def low_priority_provider():
        return _target(_span_links.SpanLinkDomain.GEVENT_GREENLET, 11)

    def high_priority_provider():
        return _target(_span_links.SpanLinkDomain.ASYNCIO_TASK, 22)

    _span_links.register_logical_span_provider(low_priority_provider, priority=10)
    _span_links.register_logical_span_provider(high_priority_provider, priority=20)
    _span_links.enable_span_linking()

    _span_links.link_span(_info(101), None)
    _span_links.unregister_logical_span_provider(high_priority_provider)
    _span_links.link_span(_info(202), None)

    assert linked == [
        (_span_links.SpanLinkDomain.ASYNCIO_TASK, 22, 101, 101, None),
        (_span_links.SpanLinkDomain.GEVENT_GREENLET, 11, 202, 202, None),
    ]


def test_provider_failure_uses_next_provider() -> None:
    def broken_provider():
        raise RuntimeError("provider failed")

    expected = _target(_span_links.SpanLinkDomain.GEVENT_GREENLET, 31)
    _span_links.register_logical_span_provider(broken_provider, priority=20)
    _span_links.register_logical_span_provider(lambda: expected, priority=10)

    assert _span_links._current_logical_span_target() == expected


def test_logical_detachment_does_not_clear_thread_link(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    cleared = []
    monkeypatch.setattr(_span_links.stack, "link_logical_span", lambda *args: linked.append(args))
    monkeypatch.setattr(_span_links.stack, "clear_logical_span", lambda *args: cleared.append(args))
    monkeypatch.setattr(_span_links.stack, "clear_span", lambda: pytest.fail("unexpected thread clear"))

    _span_links.register_logical_span_provider(lambda: _target(_span_links.SpanLinkDomain.GEVENT_GREENLET, 33))
    _span_links.enable_span_linking()
    _span_links.link_span(_info(303), None)
    _span_links.link_span(None, None)

    assert linked == [(_span_links.SpanLinkDomain.GEVENT_GREENLET, 33, 303, 303, None)]
    assert cleared == [(_span_links.SpanLinkDomain.GEVENT_GREENLET, 33)]


def test_inherited_context_seeds_logical_span_for_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    cleared = []
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: None)
    monkeypatch.setattr(_span_links.stack, "link_logical_span", lambda *args: linked.append(args))
    monkeypatch.setattr(_span_links.stack, "clear_logical_span", lambda *args: cleared.append(args))

    _span_links.enable_span_linking()
    _span_links.link_span(_info(701), None)
    inherited_context = contextvars.copy_context()
    assert _span_links.link_logical_span_context(_span_links.SpanLinkDomain.ASYNCIO_TASK, 71, inherited_context)

    _span_links.disable_span_linking()
    _span_links.enable_span_linking()
    assert not _span_links.link_logical_span_context(_span_links.SpanLinkDomain.ASYNCIO_TASK, 72, inherited_context)

    assert linked == [(_span_links.SpanLinkDomain.ASYNCIO_TASK, 71, 701, 701, None)]
    assert cleared == [(_span_links.SpanLinkDomain.ASYNCIO_TASK, 72)]


def test_inherited_context_rejects_finished_span(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    cleared = []
    monkeypatch.setattr(_span_links.stack, "link_span", lambda *args: None)
    monkeypatch.setattr(_span_links.stack, "link_logical_span", lambda *args: linked.append(args))
    monkeypatch.setattr(_span_links.stack, "clear_logical_span", lambda *args: cleared.append(args))

    span = Span("test")
    _span_links.enable_span_linking()
    _span_links.link_span(_info(span.span_id), span)
    inherited_context = contextvars.copy_context()
    span.finish()

    assert not _span_links.link_logical_span_context(_span_links.SpanLinkDomain.ASYNCIO_TASK, 73, inherited_context)
    assert linked == []
    assert cleared == [(_span_links.SpanLinkDomain.ASYNCIO_TASK, 73)]


def test_postfork_reset_invalidates_all_inherited_span_link_state(monkeypatch: pytest.MonkeyPatch) -> None:
    resets = []
    monkeypatch.setattr(_span_links.stack, "reset_span_links", lambda: resets.append(True))

    _span_links.enable_span_linking()
    _span_links.link_span(_info(701), None)
    generation = _span_links._span_link_generation
    _span_links._reset_span_link_state()

    assert resets == [True, True]
    assert _span_links._span_link_generation == generation + 1
    assert _span_links._active_span_link.get() is None


def test_collector_postfork_reset_restores_active_span(monkeypatch: pytest.MonkeyPatch) -> None:
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
    calls = []
    monkeypatch.setattr(_span_links, "safe_contextvar_set", lambda variable, value: calls.append((variable, value)))
    value = _span_links._SpanLinkContext(1, _span_links._SpanInfo(2, 3, "web"), None)

    _span_links._set_active_span_link(value)

    assert calls == [(_span_links._active_span_link, value)]
