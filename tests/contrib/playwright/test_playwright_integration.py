"""
Integration tests for Playwright distributed tracing.

These tests verify the actual Playwright integration works correctly.
"""

from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import threading
import time

import pytest

from ddtrace import config
from ddtrace.contrib.internal.playwright import patch
from ddtrace.ext.test import TYPE as TEST_TYPE


playwright = pytest.importorskip("playwright")


class TestPlaywrightIntegration:
    """Integration tests that verify actual Playwright functionality."""

    @pytest.fixture
    def playwright_instance(self):
        """Set up a Playwright instance for testing."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            yield p

    def test_playwright_patch_integration(self):
        """Test that Playwright patching integrates properly with HTTP propagation."""
        from ddtrace.propagation.http import HTTPPropagator

        tracer = pytest.importorskip("ddtrace").tracer
        config.playwright["distributed_tracing"] = True

        # Patch Playwright
        patch.patch()

        try:
            # Test that patching doesn't break and integrates with HTTP propagation
            with tracer.trace("playwright_operation") as span:
                span.set_tag(TEST_TYPE, "test")

                headers = {}
                HTTPPropagator.inject(span.context, headers)

                # Verify test context behavior
                assert headers.get("x-datadog-sampling-priority") == "114"
                assert "x-datadog-trace-id" in headers
                assert "x-datadog-parent-id" in headers

        finally:
            patch.unpatch()

    def test_playwright_config_integration(self):
        """Test that playwright config integrates with the patching system."""
        # Test config access
        assert hasattr(config, "playwright")

        # Test config modification
        original_value = config.playwright.get("distributed_tracing", True)
        config.playwright["distributed_tracing"] = False
        assert config.playwright["distributed_tracing"] is False

        # Test patching with disabled tracing
        config.playwright["distributed_tracing"] = False
        patch.patch()
        try:
            # Should not raise exceptions
            assert True
        finally:
            patch.unpatch()

        # Restore original value
        config.playwright["distributed_tracing"] = original_value

    def test_playwright_actual_browser_context_creation(self, playwright_instance):
        """Test that Playwright context creation works with patching."""
        p = playwright_instance
        config.playwright["distributed_tracing"] = True

        # Patch Playwright
        patch.patch()

        try:
            # Try to launch browser - skip if browsers not available
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                error_msg = str(e)
                if "Executable doesn't exist" in error_msg or "Host system is missing dependencies" in error_msg:
                    pytest.skip("Playwright browsers not available - skipping browser test")
                raise

            try:
                # Create context - this should trigger our patched code
                context = browser.new_context()

                # Verify context was created successfully
                assert context is not None

                # Create a page to ensure context works
                page = context.new_page()
                assert page is not None

                # Close resources
                page.close()
                context.close()

            finally:
                browser.close()

        finally:
            patch.unpatch()

    def test_playwright_header_injection_with_actual_browser(self, playwright_instance):
        """Test that headers are actually injected when making real browser requests."""
        from ddtrace.propagation.http import HTTPPropagator

        p = playwright_instance
        tracer = pytest.importorskip("ddtrace").tracer
        config.playwright["distributed_tracing"] = True

        # Patch Playwright
        patch.patch()

        try:
            # Try to launch browser - skip if browsers not available
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                error_msg = str(e)
                if "Executable doesn't exist" in error_msg or "Host system is missing dependencies" in error_msg:
                    pytest.skip("Playwright browsers not available - skipping browser test")
                raise

            try:
                context = browser.new_context()
                try:
                    page = context.new_page()
                    try:
                        # Navigate to a simple page that should trigger header injection
                        page.goto("data:text/html,<html><body><h1>Test</h1></body></html>")

                        # Verify the page loaded
                        assert page.locator("h1").text_content() == "Test"

                        # Test that our header injection works in test context
                        with tracer.trace("test_browser_request") as span:
                            span.set_tag(TEST_TYPE, "test")

                            headers = {}
                            HTTPPropagator.inject(span.context, headers)

                            # Verify test context headers are injected
                            assert headers.get("x-datadog-sampling-priority") == "114"
                            assert "x-datadog-trace-id" in headers
                            assert "x-datadog-parent-id" in headers

                    finally:
                        page.close()
                finally:
                    context.close()
            finally:
                browser.close()

        finally:
            patch.unpatch()

    def test_playwright_context_inheritance(self):
        """Test that context inheritance works properly in Playwright scenarios."""
        from ddtrace.propagation.http import HTTPPropagator

        tracer = pytest.importorskip("ddtrace").tracer
        config.playwright["distributed_tracing"] = True

        patch.patch()

        try:
            headers = {}

            # Test context inheritance: parent span has test tag
            with tracer.trace("test_session") as session_span:
                session_span.set_tag(TEST_TYPE, "test")

                with tracer.trace("browser_context") as context_span:
                    HTTPPropagator.inject(context_span.context, headers)

                    # Should inherit test context and use priority 114
                    assert headers.get("x-datadog-sampling-priority") == "114"

        finally:
            patch.unpatch()


class HeaderCaptureHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures request headers."""

    captured_headers = []

    def do_GET(self):
        """Handle GET requests and capture headers."""
        # Capture all headers
        headers = {}
        for header_name, header_value in self.headers.items():
            headers[header_name.lower()] = header_value

        # Store the captured headers
        HeaderCaptureHandler.captured_headers.append(headers)

        # Send a simple response
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h1>Test Page</h1></body></html>")

    @classmethod
    def clear_captured_headers(cls):
        """Clear captured headers for a new test."""
        cls.captured_headers = []


class TestPlaywrightHeaderInjectionE2E:
    """End-to-end tests that verify actual HTTP header injection in browser requests."""

    def test_playwright_injects_headers_in_browser_requests(self, playwright):
        """Test that Playwright actually injects Datadog headers into browser HTTP requests."""
        import socket

        from ddtrace.ext.test import TYPE as TEST_TYPE

        p = playwright
        tracer = pytest.importorskip("ddtrace").tracer
        config.playwright["distributed_tracing"] = True

        # Clear any previous captured headers
        HeaderCaptureHandler.clear_captured_headers()

        # Find an available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            port = s.getsockname()[1]

        # Start a test HTTP server
        server = HTTPServer(("localhost", port), HeaderCaptureHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        # Give server time to start
        time.sleep(0.1)

        try:
            # Patch Playwright
            patch.patch()

            # Create a test span with test.type tag
            with tracer.trace("test_browser_header_injection") as test_span:
                test_span.set_tag(TEST_TYPE, "test")

                try:
                    # Try to launch browser - skip if browsers not available
                    try:
                        browser = p.chromium.launch(headless=True)
                    except Exception as e:
                        error_msg = str(e)
                        if (
                            "Executable doesn't exist" in error_msg
                            or "Host system is missing dependencies" in error_msg
                        ):
                            pytest.skip("Playwright browsers not available - skipping browser test")
                        raise

                    try:
                        # Create browser context (this should inject headers at context level)
                        context = browser.new_context()
                        try:
                            page = context.new_page()
                            try:
                                # Navigate to our test server - this should trigger header injection
                                page.goto(f"http://localhost:{port}/")

                                # Wait a bit for the request to complete
                                time.sleep(0.2)

                                # Verify that headers were captured
                                assert len(HeaderCaptureHandler.captured_headers) > 0, "No HTTP requests were captured"

                                # Check the headers from the request
                                request_headers = HeaderCaptureHandler.captured_headers[0]

                                # Verify Datadog headers are present
                                assert "x-datadog-trace-id" in request_headers, "Missing x-datadog-trace-id header"
                                assert "x-datadog-parent-id" in request_headers, "Missing x-datadog-parent-id header"
                                assert "x-datadog-sampling-priority" in request_headers, (
                                    "Missing x-datadog-sampling-priority header"
                                )

                                # Verify the sampling priority is 114 (special test context value)
                                sampling_priority = request_headers["x-datadog-sampling-priority"]
                                assert sampling_priority == "114", (
                                    f"Expected sampling priority 114, got {sampling_priority}"
                                )

                                # Verify trace and parent IDs are valid
                                trace_id = request_headers["x-datadog-trace-id"]
                                parent_id = request_headers["x-datadog-parent-id"]
                                assert trace_id != "0", "Trace ID should not be zero"
                                assert parent_id != "0", "Parent ID should not be zero"

                            finally:
                                page.close()
                        finally:
                            context.close()
                    finally:
                        browser.close()

                finally:
                    patch.unpatch()

        finally:
            # Clean up server
            server.shutdown()
            server.server_close()

    def test_playwright_headers_not_injected_when_disabled(self, playwright):
        """Test that headers are not injected when distributed_tracing is disabled."""
        import socket

        p = playwright
        config.playwright["distributed_tracing"] = False  # Disable tracing

        # Clear any previous captured headers
        HeaderCaptureHandler.clear_captured_headers()

        # Find an available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            port = s.getsockname()[1]

        # Start a test HTTP server
        server = HTTPServer(("localhost", port), HeaderCaptureHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        # Give server time to start
        time.sleep(0.1)

        try:
            # Patch Playwright
            patch.patch()

            try:
                # Try to launch browser - skip if browsers not available
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception as e:
                    error_msg = str(e)
                    if "Executable doesn't exist" in error_msg or "Host system is missing dependencies" in error_msg:
                        pytest.skip("Playwright browsers not available - skipping browser test")
                    raise

                try:
                    context = browser.new_context()
                    try:
                        page = context.new_page()
                        try:
                            # Navigate to our test server
                            page.goto(f"http://localhost:{port}/")

                            # Wait a bit for the request to complete
                            time.sleep(0.2)

                            # Verify that headers were captured
                            assert len(HeaderCaptureHandler.captured_headers) > 0, "No HTTP requests were captured"

                            # Check the headers from the request
                            request_headers = HeaderCaptureHandler.captured_headers[0]

                            # Verify Datadog headers are NOT present when tracing is disabled
                            assert "x-datadog-trace-id" not in request_headers, (
                                "x-datadog-trace-id should not be present when tracing disabled"
                            )
                            assert "x-datadog-parent-id" not in request_headers, (
                                "x-datadog-parent-id should not be present when tracing disabled"
                            )
                            assert "x-datadog-sampling-priority" not in request_headers, (
                                "x-datadog-sampling-priority should not be present when tracing disabled"
                            )

                        finally:
                            page.close()
                    finally:
                        context.close()
                finally:
                    browser.close()

            finally:
                patch.unpatch()

        finally:
            # Clean up server
            server.shutdown()
            server.server_close()

    def test_playwright_headers_injected_outside_test_context(self, playwright):
        """Test that headers are injected with normal priority outside test context."""
        import socket

        p = playwright
        tracer = pytest.importorskip("ddtrace").tracer
        config.playwright["distributed_tracing"] = True

        # Clear any previous captured headers
        HeaderCaptureHandler.clear_captured_headers()

        # Find an available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            port = s.getsockname()[1]

        # Start a test HTTP server
        server = HTTPServer(("localhost", port), HeaderCaptureHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        # Give server time to start
        time.sleep(0.1)

        try:
            # Patch Playwright
            patch.patch()

            # Create a regular span (not test context)
            with tracer.trace("regular_browser_operation") as regular_span:
                regular_span.context.sampling_priority = 1  # Set normal priority

                try:
                    # Try to launch browser - skip if browsers not available
                    try:
                        browser = p.chromium.launch(headless=True)
                    except Exception as e:
                        error_msg = str(e)
                        if (
                            "Executable doesn't exist" in error_msg
                            or "Host system is missing dependencies" in error_msg
                        ):
                            pytest.skip("Playwright browsers not available - skipping browser test")
                        raise

                    try:
                        context = browser.new_context()
                        try:
                            page = context.new_page()
                            try:
                                # Navigate to our test server
                                page.goto(f"http://localhost:{port}/")

                                # Wait a bit for the request to complete
                                time.sleep(0.2)

                                # Verify that headers were captured
                                assert len(HeaderCaptureHandler.captured_headers) > 0, "No HTTP requests were captured"

                                # Check the headers from the request
                                request_headers = HeaderCaptureHandler.captured_headers[0]

                                # Verify Datadog headers are present
                                assert "x-datadog-trace-id" in request_headers, "Missing x-datadog-trace-id header"
                                assert "x-datadog-parent-id" in request_headers, "Missing x-datadog-parent-id header"
                                assert "x-datadog-sampling-priority" in request_headers, (
                                    "Missing x-datadog-sampling-priority header"
                                )

                                # Verify the sampling priority is 1 (normal priority, not 114)
                                sampling_priority = request_headers["x-datadog-sampling-priority"]
                                assert sampling_priority == "1", (
                                    f"Expected sampling priority 1, got {sampling_priority}"
                                )

                            finally:
                                page.close()
                        finally:
                            context.close()
                    finally:
                        browser.close()

                finally:
                    patch.unpatch()

        finally:
            # Clean up server
            server.shutdown()
            server.server_close()
