"""Tests for profiler span-link lifecycle independent of execution integrations."""

from __future__ import annotations

import contextvars
from types import SimpleNamespace

import pytest

from ddtrace._trace.context import Context
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.internal.datadog.profiling import stack as stack_module


pytestmark = pytest.mark.skipif(not stack_module.is_available, reason="stack profiler not available")


@pytest.fixture(autouse=True)
def restore_span_linking_state():
    enabled = stack_module._span_linking_enabled
    generation = stack_module._span_link_generation
    active_span_link = stack_module._active_span_link.get()
    providers = list(stack_module._logical_span_providers)
    target_spans = dict(stack_module._target_spans)
    span_targets = {span_id: set(targets) for span_id, targets in stack_module._span_targets.items()}
    stack_module._span_linking_enabled = False
    stack_module._active_span_link.set(None)
    stack_module._logical_span_providers.clear()
    stack_module._reset_span_link_state()
    try:
        yield
    finally:
        stack_module._span_linking_enabled = enabled
        stack_module._span_link_generation = generation
        stack_module._active_span_link.set(active_span_link)
        stack_module._logical_span_providers[:] = providers
        stack_module._target_spans.clear()
        stack_module._target_spans.update(target_spans)
        stack_module._span_targets.clear()
        stack_module._span_targets.update(span_targets)


def test_span_activation_uses_highest_priority_logical_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    monkeypatch.setattr(stack_module._stack, "link_logical_span", lambda *args: linked.append(args))
    monkeypatch.setattr(stack_module._stack, "get_thread_id", lambda: pytest.fail("unexpected thread fallback"))

    def low_priority_provider():
        return 11

    def high_priority_provider():
        return 22

    stack_module.register_logical_span_provider(low_priority_provider, priority=10)
    stack_module.register_logical_span_provider(high_priority_provider, priority=20)
    stack_module.enable_span_linking()

    stack_module.link_span(Context(trace_id=1, span_id=101))
    stack_module.unregister_logical_span_provider(high_priority_provider)
    stack_module.link_span(Context(trace_id=2, span_id=202))

    assert linked == [(22, 101, 101, None), (11, 202, 202, None)]


def test_logical_detachment_does_not_clear_thread_link(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    cleared = []
    monkeypatch.setattr(stack_module._stack, "link_logical_span", lambda *args: linked.append(args))
    monkeypatch.setattr(stack_module._stack, "clear_logical_span", lambda logical_id: cleared.append(logical_id))
    monkeypatch.setattr(stack_module._stack, "clear_span", lambda: pytest.fail("unexpected thread clear"))

    stack_module.register_logical_span_provider(lambda: 33)
    stack_module.enable_span_linking()
    stack_module.link_span(Context(trace_id=1, span_id=303))
    stack_module.link_span(None)

    assert linked == [(33, 303, 303, None)]
    assert cleared == [33]


def test_finished_span_unlinks_its_original_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    unlinked = []
    monkeypatch.setattr(stack_module._stack, "get_thread_id", lambda: 44)
    monkeypatch.setattr(stack_module._stack, "link_span", lambda *args: linked.append(args))
    monkeypatch.setattr(stack_module._stack, "unlink_thread_span", lambda *args: unlinked.append(args))

    stack_module.enable_span_linking()
    stack_module.link_span(Context(trace_id=1, span_id=404))
    stack_module.unlink_finished_span(SimpleNamespace(span_id=404))

    assert linked == [(404, 404, None)]
    assert unlinked == [(44, 404)]


def test_finishing_local_root_invalidates_copied_logical_descendant(monkeypatch: pytest.MonkeyPatch) -> None:
    unlinked = []
    monkeypatch.setattr(stack_module._stack, "link_logical_span", lambda *args: None)
    monkeypatch.setattr(stack_module._stack, "unlink_logical_span", lambda *args: unlinked.append(args))

    descendant = Context(trace_id=1, span_id=502)
    context_meta.attach_profiler_link(descendant, local_root_span_id=501, span_type="web")

    stack_module.enable_span_linking()
    stack_module.link_logical_span(51, descendant)
    stack_module.link_logical_span(52, descendant)
    stack_module.link_logical_span(51, Context(trace_id=2, span_id=601))
    stack_module.unlink_finished_span(SimpleNamespace(span_id=501))

    assert unlinked == [(52, 502)]


def test_inherited_context_seeds_logical_span_for_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    linked = []
    cleared = []
    monkeypatch.setattr(stack_module._stack, "get_thread_id", lambda: 70)
    monkeypatch.setattr(stack_module._stack, "link_span", lambda *args: None)
    monkeypatch.setattr(stack_module._stack, "link_logical_span", lambda *args: linked.append(args))
    monkeypatch.setattr(stack_module._stack, "clear_logical_span", lambda logical_id: cleared.append(logical_id))

    stack_module.enable_span_linking()
    stack_module.link_span(Context(trace_id=1, span_id=701))
    inherited_context = contextvars.copy_context()
    assert stack_module.link_logical_span_context(71, inherited_context)

    stack_module.disable_span_linking()
    stack_module.enable_span_linking()
    assert not stack_module.link_logical_span_context(72, inherited_context)

    assert linked == [(71, 701, 701, None)]
    assert cleared == [72]


def test_clear_logical_span_removes_finished_span_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared = []
    unlinked = []
    monkeypatch.setattr(stack_module._stack, "link_logical_span", lambda *args: None)
    monkeypatch.setattr(stack_module._stack, "clear_logical_span", lambda logical_id: cleared.append(logical_id))
    monkeypatch.setattr(stack_module._stack, "unlink_logical_span", lambda *args: unlinked.append(args))

    stack_module.enable_span_linking()
    stack_module.link_logical_span(61, Context(trace_id=1, span_id=602))
    stack_module.clear_logical_span(61)
    stack_module.unlink_finished_span(SimpleNamespace(span_id=602))

    assert cleared == [61]
    assert unlinked == []
