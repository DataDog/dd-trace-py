"""
Integration tests for hardcoded secret detection and reporting.
"""

import json

from ddtrace.appsec._iast._hardcoded_secret_reporter import _create_standalone_vulnerability_trace
from ddtrace.appsec._iast._hardcoded_secret_reporter import report_hardcoded_secret
from ddtrace.appsec._iast.constants import VULN_HARDCODED_SECRET
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Vulnerability
from tests.utils import DummyTracer
from tests.utils import override_global_config


class TestHardcodedSecretIntegration:
    """Test hardcoded secret reporting with immediate reporting."""

    def test_standalone_trace_creation(self):
        """Test that standalone traces are created for hardcoded secrets when no active span."""
        # Create a vulnerability
        evidence = Evidence(value="aws-access-token")
        location = Location(spanId=0, path="test.py", line=10)
        vulnerability = Vulnerability(
            type=VULN_HARDCODED_SECRET,
            evidence=evidence,
            location=location,
        )

        # Use DummyTracer to capture the span
        tracer = DummyTracer()

        with override_global_config({"_dd_api_version": "v0.4"}):
            # Patch the global tracer
            import ddtrace

            original_tracer = ddtrace.tracer
            try:
                ddtrace.tracer = tracer
                ddtrace.appsec._iast._hardcoded_secret_reporter.tracer = tracer

                # Create standalone trace (no active span)
                _create_standalone_vulnerability_trace(vulnerability)

                # Verify a span was created
                traces = tracer.pop()
                assert len(traces) == 1
                span = traces[0]

                # Verify span properties
                assert span.name == "vulnerability"
                assert span.span_type == "vulnerability"
                assert span.get_tag("_dd.iast.enabled") == 1
                assert span.get_tag("dd.manual.keep") is not None

                # Verify vulnerability data was added
                iast_json = span.get_tag("_dd.iast.json")
                assert iast_json is not None

                # Parse and verify the JSON
                vulnerability_data = json.loads(iast_json)
                assert "vulnerabilities" in vulnerability_data
                assert len(vulnerability_data["vulnerabilities"]) == 1

                reported_vuln = vulnerability_data["vulnerabilities"][0]
                assert reported_vuln["type"] == VULN_HARDCODED_SECRET
                assert reported_vuln["evidence"]["value"] == "aws-access-token"
                assert reported_vuln["location"]["path"] == "test.py"
                assert reported_vuln["location"]["line"] == 10

            finally:
                ddtrace.tracer = original_tracer
                ddtrace.appsec._iast._hardcoded_secret_reporter.tracer = original_tracer

    def test_immediate_reporting_without_span(self):
        """Test that report_hardcoded_secret creates standalone trace when no active span."""
        evidence = Evidence(value="github-pat")
        location = Location(spanId=0, path="config.py", line=5)
        vulnerability = Vulnerability(
            type=VULN_HARDCODED_SECRET,
            evidence=evidence,
            location=location,
        )

        tracer = DummyTracer()

        with override_global_config({"_dd_api_version": "v0.4"}):
            import ddtrace

            original_tracer = ddtrace.tracer
            try:
                ddtrace.tracer = tracer
                ddtrace.appsec._iast._hardcoded_secret_reporter.tracer = tracer

                # Report immediately (like from AST visitor)
                report_hardcoded_secret(vulnerability)

                # Verify standalone trace was created
                traces = tracer.pop()
                assert len(traces) == 1
                span = traces[0]

                assert span.name == "vulnerability"
                assert span.span_type == "vulnerability"

            finally:
                ddtrace.tracer = original_tracer
                ddtrace.appsec._iast._hardcoded_secret_reporter.tracer = original_tracer
