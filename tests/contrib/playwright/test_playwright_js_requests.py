"""
Test that Playwright integration works with JavaScript-initiated requests (fetch, XMLHttpRequest).

This is critical because extra_http_headers should apply to ALL requests from the browser,
not just navigation requests like page.goto().
"""

from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import socket
import threading
import time

import pytest

from ddtrace import config
from ddtrace.contrib.internal.playwright import patch
from ddtrace.ext.test import TYPE as TEST_TYPE


playwright = pytest.importorskip("playwright")


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

        # Send a simple response with CORS headers
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, fmt, *args):
        """Suppress log messages."""
        pass

    @classmethod
    def clear_captured_headers(cls):
        """Clear captured headers for a new test."""
        cls.captured_headers = []


class TestPlaywrightJavaScriptRequests:
    """Test that Playwright injects headers into JavaScript-initiated requests."""

    def test_fetch_requests_include_tracing_headers(self, playwright):
        """Test that JavaScript fetch() requests include Datadog tracing headers."""
        p = playwright
        tracer = pytest.importorskip("ddtrace").tracer
        config.playwright["distributed_tracing"] = True

        # Clear any previous captured headers
        HeaderCaptureHandler.clear_captured_headers()

        # Find an available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
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
                # Create a test span with test.type tag
                with tracer.trace("test_js_fetch") as test_span:
                    test_span.set_tag(TEST_TYPE, "test")

                    # Try to launch browser
                    try:
                        browser = p.chromium.launch(headless=True)
                    except Exception as e:
                        error_msg = str(e)
                        if (
                            "Executable doesn't exist" in error_msg
                            or "Host system is missing dependencies" in error_msg
                        ):
                            pytest.skip("Playwright browsers not available")
                        raise

                    try:
                        # Create browser context with headers
                        context = browser.new_context()
                        try:
                            page = context.new_page()
                            try:
                                # Navigate to a simple HTML page first
                                page.goto(f"http://localhost:{port}/")

                                # Clear captured headers so we only see the fetch request
                                HeaderCaptureHandler.clear_captured_headers()

                                # Execute JavaScript to make a fetch request
                                page.evaluate(f"""
                                    fetch('http://localhost:{port}/api/data')
                                        .then(r => r.text())
                                        .catch(err => console.error(err));
                                """)

                                # Wait for the fetch to complete
                                time.sleep(0.5)

                                # Verify the fetch request was captured
                                assert len(HeaderCaptureHandler.captured_headers) >= 1, (
                                    f"Expected at least 1 fetch request, got {len(HeaderCaptureHandler.captured_headers)}"
                                )

                                # Check the fetch request headers (should be the first/only one now)
                                fetch_headers = HeaderCaptureHandler.captured_headers[0]

                                # Verify Datadog headers
                                assert "x-datadog-trace-id" in fetch_headers, (
                                    f"Missing trace-id in fetch. Headers: {list(fetch_headers.keys())}"
                                )
                                assert "x-datadog-parent-id" in fetch_headers, "Missing parent-id in fetch"
                                assert "x-datadog-sampling-priority" in fetch_headers, (
                                    "Missing sampling-priority in fetch"
                                )

                                # Verify test context sampling priority
                                priority = fetch_headers["x-datadog-sampling-priority"]
                                assert priority == "114", f"Expected priority 114, got {priority}"

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
