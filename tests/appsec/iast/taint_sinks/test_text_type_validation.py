"""Regression tests for text type validation in taint sink functions.

This module validates the isinstance(obj, IAST.TEXT_TYPES) checks to prevent vulnerability reporting
for non-text types in various taint sink functions.

The tests ensure that taint sink functions only report vulnerabilities for text types
(str, bytes, bytearray) and ignore non-text types even if they are tainted.
"""

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.taint_sinks.command_injection import _iast_report_cmdi
from ddtrace.appsec._iast.taint_sinks.path_traversal import check_and_report_path_traversal
from ddtrace.appsec._iast.taint_sinks.unvalidated_redirect import _iast_report_unvalidated_redirect
from ddtrace.appsec._iast.taint_sinks.xss import _iast_report_xss


def test_text_types_still_trigger_vulnerabilities(iast_context_defaults):
    """Test that str, bytes, and bytearray still trigger vulnerability reports."""
    text_types = [
        ("tainted_string", str),
        (b"tainted_bytes", bytes),
        (bytearray(b"tainted_bytearray"), bytearray),
    ]

    for text_obj, text_type in text_types:
        # Reset context for each test
        span_report = get_iast_reporter()
        if span_report:
            span_report.vulnerabilities.clear()

        # Taint the text object
        tainted_obj = taint_pyobject(
            text_obj,
            source_name="test_source",
            source_value=str(text_obj),
            source_origin=OriginType.PARAMETER,
        )

        # Test command injection - should report vulnerability
        _iast_report_cmdi(tainted_obj)
        span_report = get_iast_reporter()
        assert span_report is not None, f"No vulnerability reported for {text_type.__name__}"
        assert len(span_report.vulnerabilities) > 0, f"No vulnerabilities found for {text_type.__name__}"

        # Clear vulnerabilities for next test
        span_report.vulnerabilities.clear()

        # Test XSS - should report vulnerability
        _iast_report_xss(tainted_obj)
        span_report = get_iast_reporter()
        assert span_report is not None, f"No XSS vulnerability reported for {text_type.__name__}"
        assert len(span_report.vulnerabilities) > 0, f"No XSS vulnerabilities found for {text_type.__name__}"

        # Clear vulnerabilities for next test
        span_report.vulnerabilities.clear()

        # Test path traversal - should report vulnerability
        check_and_report_path_traversal(tainted_obj)
        span_report = get_iast_reporter()
        assert span_report is not None, f"No path traversal vulnerability reported for {text_type.__name__}"
        assert len(span_report.vulnerabilities) > 0, f"No path traversal vulnerabilities found for {text_type.__name__}"

        # Clear vulnerabilities for next test
        span_report.vulnerabilities.clear()

        # Test unvalidated redirect - should report vulnerability
        _iast_report_unvalidated_redirect(tainted_obj)
        span_report = get_iast_reporter()
        assert span_report is not None, f"No unvalidated redirect vulnerability reported for {text_type.__name__}"
        assert (
            len(span_report.vulnerabilities) > 0
        ), f"No unvalidated redirect vulnerabilities found for {text_type.__name__}"

        # Clear vulnerabilities for next test
        span_report.vulnerabilities.clear()
