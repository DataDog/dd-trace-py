"""Tests for physical profiler span-link cleanup."""

import pytest

from ddtrace.internal.datadog.profiling import stack as stack_module


pytestmark = pytest.mark.skipif(not stack_module.is_available, reason="stack profiler not available")


def test_context_deactivation_clears_physical_span_link(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared = []
    monkeypatch.setattr(stack_module._stack, "clear_span", lambda: cleared.append(True))

    stack_module.link_span(None)

    assert cleared == [True]
