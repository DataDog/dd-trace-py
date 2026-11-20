"""Tests for physical profiler span-link cleanup."""

import pytest

from ddtrace.internal.datadog.profiling import stack as stack_module
from ddtrace.profiling.collector.stack import StackCollector
from ddtrace.trace import Tracer


pytestmark = pytest.mark.skipif(not stack_module.is_available, reason="stack profiler not available")


def test_context_deactivation_clears_physical_span_link(monkeypatch: pytest.MonkeyPatch, tracer: Tracer) -> None:
    cleared = []
    monkeypatch.setattr(stack_module, "clear_span", lambda task_id: cleared.append(task_id))

    collector = StackCollector(tracer=tracer)
    collector._link_span(tracer.context_provider, None)

    # Outside an asyncio task, there is no task link to clear.
    assert cleared == [0]
