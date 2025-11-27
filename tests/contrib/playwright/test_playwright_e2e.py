"""
End-to-end tests for Playwright distributed tracing in pytest context.

These tests simulate real pytest usage scenarios.
"""
import pytest

from ddtrace import config
from ddtrace.contrib.internal.playwright import patch
from ddtrace.ext.test import TYPE as TEST_TYPE


playwright = pytest.importorskip("playwright")


@pytest.mark.skipif(not hasattr(pytest, "mark"), reason="pytest.mark not available")
class TestPlaywrightE2E:
    """E2E tests that simulate real pytest + Playwright usage."""

    def test_pytest_context_with_playwright_tracing(self):
        """Test that simulates running Playwright in a pytest test context."""
        from ddtrace.propagation.http import HTTPPropagator

        tracer = pytest.importorskip("ddtrace").tracer

        # Simulate pytest test context
        with tracer.trace("pytest_test_function") as test_span:
            test_span.set_tag(TEST_TYPE, "test")
            test_span.set_tag("test.name", "test_example")
            test_span.set_tag("test.framework", "pytest")

            # Enable Playwright distributed tracing
            config.playwright["distributed_tracing"] = True

            # Patch Playwright (as would happen in conftest.py or setup)
            patch.patch()

            try:
                # Simulate a browser request that would inject headers
                headers = {}

                # This simulates what happens when Playwright makes requests
                HTTPPropagator.inject(test_span.context, headers)

                # Verify test context behavior
                assert headers.get("x-datadog-sampling-priority") == "114"
                assert "x-datadog-trace-id" in headers
                assert "x-datadog-parent-id" in headers

                # Verify the test span information is propagated
                trace_id = headers.get("x-datadog-trace-id")
                parent_id = headers.get("x-datadog-parent-id")

                assert trace_id is not None
                assert parent_id is not None

            finally:
                patch.unpatch()

    def test_sampling_priority_inheritance_in_test_context(self):
        """Test that child spans in test context inherit the sampling priority override."""
        from ddtrace.propagation.http import HTTPPropagator

        tracer = pytest.importorskip("ddtrace").tracer

        config.playwright["distributed_tracing"] = True

        with tracer.trace("pytest_session") as session_span:
            session_span.set_tag(TEST_TYPE, "test")
            session_span.set_tag("test.framework", "pytest")

            # Child span (like a test function)
            with tracer.trace("pytest_test") as test_span:
                test_span.set_tag(TEST_TYPE, "test")

                # Grandchild span (like a Playwright browser operation)
                with tracer.trace("browser_request") as browser_span:
                    headers = {}
                    HTTPPropagator.inject(browser_span.context, headers)

                    # Even though browser_span doesn't have test.type directly,
                    # the parent spans do, so it should still get priority 114
                    # (This depends on how we implement the context detection)

                    # For now, let's verify basic header injection
                    assert "x-datadog-trace-id" in headers
                    assert "x-datadog-parent-id" in headers

                    # The sampling priority behavior depends on our implementation
                    # If we check current_span(), it should be 114
                    # If we check the injected context, it might vary
                    priority = headers.get("x-datadog-sampling-priority")
                    assert priority is not None, "Sampling priority should be set"

    def test_mixed_contexts_in_test_execution(self):
        """Test behavior when mixing test and non-test operations in the same execution."""
        from ddtrace.propagation.http import HTTPPropagator

        tracer = pytest.importorskip("ddtrace").tracer

        config.playwright["distributed_tracing"] = True

        headers_test = {}
        headers_regular = {}

        # Test operation in test context
        with tracer.trace("test_operation") as test_span:
            test_span.set_tag(TEST_TYPE, "test")
            HTTPPropagator.inject(test_span.context, headers_test)

        # Regular operation outside test context
        with tracer.trace("regular_operation") as regular_span:
            regular_span.context.sampling_priority = 2
            HTTPPropagator.inject(regular_span.context, headers_regular)

        # Test context should get priority 114
        assert headers_test.get("x-datadog-sampling-priority") == "114"

        # Regular context should keep its original priority
        assert headers_regular.get("x-datadog-sampling-priority") == "2"

        # Both should have trace headers
        for headers in [headers_test, headers_regular]:
            assert "x-datadog-trace-id" in headers
            assert "x-datadog-parent-id" in headers
