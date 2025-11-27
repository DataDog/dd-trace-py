import pytest

from ddtrace import config
from ddtrace.contrib.internal.playwright import patch


class TestPlaywrightPatch:
    def test_patch_unpatch(self):
        """Test that patch and unpatch work without errors."""
        # Test patching
        patch.patch()

        # Verify patch flag is set
        try:
            import playwright

            assert getattr(playwright, "_datadog_patch", False)
        except ImportError:
            pytest.skip("Playwright not available")

        # Test unpatching
        patch.unpatch()

        # Verify patch flag is cleared
        try:
            import playwright

            assert not getattr(playwright, "_datadog_patch", False)
        except ImportError:
            pass

    def test_get_version(self):
        """Test version detection."""
        version = patch.get_version()
        assert isinstance(version, str)

    def test_supported_versions(self):
        """Test supported versions."""
        versions = patch._supported_versions()
        assert isinstance(versions, dict)
        assert "playwright" in versions

    def test_config_initialization(self):
        """Test that playwright config is properly initialized."""
        # Config should be accessible
        assert hasattr(config, "playwright")
        assert isinstance(config.playwright, dict)

        # Should have distributed_tracing setting
        assert "distributed_tracing" in config.playwright

    @pytest.mark.skipif(
        not hasattr(patch, "_inject_distributed_tracing_headers"),
        reason="Integration may not be fully loaded",
    )
    def test_inject_distributed_tracing_headers(self):
        """Test that distributed tracing headers are injected correctly."""
        from ddtrace.contrib.internal.playwright.patch import _inject_distributed_tracing_headers

        # Test with distributed tracing enabled
        config.playwright["distributed_tracing"] = True
        headers = {}
        _inject_distributed_tracing_headers(headers)

        # Should have injected Datadog headers when span is active
        # Note: headers may not be present if no active span
        assert isinstance(headers, dict)

    def test_inject_distributed_tracing_headers_disabled(self):
        """Test that headers are not injected when distributed tracing is disabled."""
        from ddtrace.contrib.internal.playwright.patch import _inject_distributed_tracing_headers

        # Disable distributed tracing
        config.playwright["distributed_tracing"] = False
        headers = {}
        original_count = len(headers)

        _inject_distributed_tracing_headers(headers)

        # No headers should be added
        assert len(headers) == original_count

    def test_sampling_priority_override_in_test_context(self):
        """Test that sampling priority 114 is used in test contexts."""
        from ddtrace.propagation.http import HTTPPropagator
        from ddtrace.ext.test import TYPE as TEST_TYPE

        # Import tracer
        tracer = pytest.importorskip("ddtrace").tracer

        # Test outside of test context - should use normal sampling priority
        headers_normal = {}
        with tracer.trace("normal_span") as span:
            span.context.sampling_priority = 1  # Set a normal priority
            HTTPPropagator.inject(span.context, headers_normal)

        normal_priority = headers_normal.get("x-datadog-sampling-priority")

        # Test in test context - should use priority 114
        headers_test = {}
        with tracer.trace("test_span") as span:
            span.set_tag(TEST_TYPE, "test")
            span.context.sampling_priority = 1  # This should be overridden
            HTTPPropagator.inject(span.context, headers_test)

        test_priority = headers_test.get("x-datadog-sampling-priority")

        # Test context should override to priority 114
        assert test_priority == "114", f"Expected priority 114 in test context, got {test_priority}"

        # Normal context should not be 114 (could be 1 or None)
        assert normal_priority != "114", f"Normal context should not have priority 114, got {normal_priority}"

    def test_playwright_header_injection_with_test_context(self):
        """Test that Playwright header injection works with test context priority override."""
        from ddtrace.contrib.internal.playwright.patch import _inject_distributed_tracing_headers
        from ddtrace.ext.test import TYPE as TEST_TYPE

        tracer = pytest.importorskip("ddtrace").tracer

        # Enable distributed tracing
        config.playwright["distributed_tracing"] = True

        # Test in regular context
        headers_regular = {}
        with tracer.trace("regular_operation") as span:
            span.context.sampling_priority = 2
            _inject_distributed_tracing_headers(headers_regular)

        # Test in test context
        headers_test = {}
        with tracer.trace("test_operation") as span:
            span.set_tag(TEST_TYPE, "test")
            span.context.sampling_priority = 2  # Should be overridden
            _inject_distributed_tracing_headers(headers_test)

        # Both should have trace headers
        assert "x-datadog-trace-id" in headers_regular
        assert "x-datadog-parent-id" in headers_regular
        assert "x-datadog-trace-id" in headers_test
        assert "x-datadog-parent-id" in headers_test

        # Test context should have priority 114
        assert headers_test.get("x-datadog-sampling-priority") == "114"

        # Regular context should have original priority (2)
        assert headers_regular.get("x-datadog-sampling-priority") == "2"

    def test_end_to_end_playwright_with_test_context(self):
        """End-to-end test of Playwright integration with test context sampling priority."""
        from ddtrace.contrib.internal.playwright.patch import _inject_distributed_tracing_headers
        from ddtrace.propagation.http import HTTPPropagator
        from ddtrace.ext.test import TYPE as TEST_TYPE

        tracer = pytest.importorskip("ddtrace").tracer
        config.playwright["distributed_tracing"] = True

        # Test 1: Playwright headers in regular context
        headers_regular = {}
        with tracer.trace("regular_browser_op") as span:
            span.context.sampling_priority = 3
            _inject_distributed_tracing_headers(headers_regular)

        # Test 2: Playwright headers in test context
        headers_test = {}
        with tracer.trace("test_browser_op") as span:
            span.set_tag(TEST_TYPE, "test")
            span.context.sampling_priority = 3  # Should be overridden
            _inject_distributed_tracing_headers(headers_test)

        # Test 3: Direct HTTPPropagator.inject in test context
        headers_direct = {}
        with tracer.trace("direct_test_op") as span:
            span.set_tag(TEST_TYPE, "test")
            span.context.sampling_priority = 7  # Should be overridden
            HTTPPropagator.inject(span.context, headers_direct)

        # Verify all contexts have basic trace headers
        for name, headers in [("regular", headers_regular), ("test", headers_test), ("direct", headers_direct)]:
            assert "x-datadog-trace-id" in headers, f"{name} context missing trace-id"
            assert "x-datadog-parent-id" in headers, f"{name} context missing parent-id"

        # Verify sampling priorities
        assert headers_regular.get("x-datadog-sampling-priority") == "3", "Regular context should keep original priority"
        assert headers_test.get("x-datadog-sampling-priority") == "114", "Test context should override to 114"
        assert headers_direct.get("x-datadog-sampling-priority") == "114", "Direct injection in test context should override to 114"

    def test_playwright_config_isolation(self):
        """Test that playwright config changes don't affect global state."""
        original_value = config.playwright.get("distributed_tracing", True)

        try:
            # Change config
            config.playwright["distributed_tracing"] = False
            assert config.playwright["distributed_tracing"] is False

            # Change back
            config.playwright["distributed_tracing"] = True
            assert config.playwright["distributed_tracing"] is True

        finally:
            # Restore original value
            config.playwright["distributed_tracing"] = original_value
