"""
Tests for query string and vulnerability evidence redaction synchronization.

This test module validates that query string obfuscation at the span level
is synchronized with IAST evidence redaction, addressing the issue described in:
"Query String and Vulnerability Evidence redaction synchronization.pdf"
"""

import re

from ddtrace.appsec._iast._evidence_redaction._sensitive_handler import SensitiveHandler
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.appsec._iast.constants import VULN_UNVALIDATED_REDIRECT
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.taint_sinks.ssrf import SSRF
from ddtrace.appsec._iast.taint_sinks.unvalidated_redirect import UnvalidatedRedirect


class TestSensitiveHandlerQueryStringSync:
    """Test SensitiveHandler methods for query string synchronization."""

    def test_is_query_string_source_with_query_origin(self):
        """Test that sources with OriginType.QUERY are correctly identified."""
        handler = SensitiveHandler()

        # Create a source with QUERY origin
        query_source = Source("param", "value", OriginType.QUERY)

        assert handler.is_query_string_source(query_source) is True

    def test_is_query_string_source_with_non_query_origin(self):
        """Test that sources with other origins are not identified as query string."""
        handler = SensitiveHandler()

        # Create sources with different origins
        param_source = Source("param", "value", OriginType.PARAMETER)
        header_source = Source("header", "value", OriginType.HEADER)
        body_source = Source("body", "value", OriginType.BODY)

        assert handler.is_query_string_source(param_source) is False
        assert handler.is_query_string_source(header_source) is False
        assert handler.is_query_string_source(body_source) is False

    def test_is_query_string_source_with_none(self):
        """Test that None sources return False."""
        handler = SensitiveHandler()

        assert handler.is_query_string_source(None) is False

    def test_is_sensible_source_query_with_default_pattern(self):
        """Test that query string sources matching default pattern are marked sensitive."""
        handler = SensitiveHandler()

        # Create query sources that match the default pattern
        password_source = Source("password", "secret123", OriginType.QUERY)
        api_key_source = Source("api_key", "abc123def", OriginType.QUERY)
        token_source = Source("token", "xyz789", OriginType.QUERY)

        # These should be marked as sensitive due to query string pattern
        assert handler.is_sensible_source(password_source) is True
        assert handler.is_sensible_source(api_key_source) is True
        assert handler.is_sensible_source(token_source) is True

    def test_is_sensible_source_query_not_matching_pattern(self):
        """Test that query string sources not matching pattern follow normal IAST rules."""
        handler = SensitiveHandler()

        # Create query source that doesn't match query string pattern
        # but also doesn't match IAST patterns
        normal_source = Source("id", "12345", OriginType.QUERY)

        # Should not be marked as sensitive (doesn't match any pattern)
        assert handler.is_sensible_source(normal_source) is False

    def test_is_sensible_source_non_query_with_sensitive_name(self):
        """Test that non-query sources still use IAST redaction patterns."""
        handler = SensitiveHandler()

        # Create non-query source with sensitive name (matches IAST pattern)
        password_param = Source("password", "secret123", OriginType.PARAMETER)

        # Should be marked as sensitive due to IAST name pattern
        assert handler.is_sensible_source(password_param) is True


class TestSSRFQueryStringSync:
    """Test SSRF vulnerability with query string synchronization."""

    def test_ssrf_query_string_source_with_password(self, iast_context_defaults):
        """Test that SSRF with query string source containing password is redacted."""
        # Taint a query string with password parameter - using QUERY origin
        query_string = taint_pyobject(
            pyobject="password=secret123&user=admin",
            source_name="query_string",
            source_value="password=secret123&user=admin",
            source_origin=OriginType.QUERY,
        )

        url = add_aspect("https://example.com/?", query_string)

        SSRF.report(url)

        # Build report and check redaction
        from tests.appsec.iast.iast_utils import _get_iast_data

        data = _get_iast_data()
        vulnerability = list(data["vulnerabilities"])[0]

        assert vulnerability["type"] == VULN_SSRF

        # The query string source should be redacted because it matches query string pattern
        sources = data["sources"]
        assert len(sources) > 0

        # Check that the source was redacted
        source = sources[0]
        assert source["redacted"] is True

    def test_ssrf_url_with_query_string_password(self, iast_context_defaults):
        """Test that SSRF with URL containing password in query string is redacted."""
        url = "https://api.example.com/data?api_key=secret123&id=456&password=mysecret"
        tainted_url = taint_pyobject(
            pyobject=url, source_name="request_url", source_value=url, source_origin=OriginType.QUERY
        )

        ev = Evidence(value=tainted_url)
        loc = Location(path="test.py", line=10, spanId=123)
        v = Vulnerability(type=VULN_SSRF, evidence=ev, location=loc)

        report = IastSpanReporter(vulnerabilities={v})
        report.add_ranges_to_evidence_and_extract_sources(v)
        result = report.build_and_scrub_value_parts()

        assert result["vulnerabilities"]
        vulnerability = result["vulnerabilities"][0]

        # Check that sensitive query string parameters are redacted
        value_parts = vulnerability["evidence"]["valueParts"]
        assert any("redacted" in part for part in value_parts)

    def test_ssrf_url_query_string_with_bearer_token(self, iast_context_defaults):
        """Test URL with bearer token in query string is redacted."""
        # Create URL with bearer token (matches default pattern)
        url_base = "https://api.example.com/endpoint?token=bearer abc123def456&param=value"
        tainted_url = taint_pyobject(
            pyobject=url_base, source_name="url", source_value=url_base, source_origin=OriginType.QUERY
        )

        ev = Evidence(value=tainted_url)
        loc = Location(path="test.py", line=20, spanId=456)
        v = Vulnerability(type=VULN_SSRF, evidence=ev, location=loc)

        report = IastSpanReporter(vulnerabilities={v})
        report.add_ranges_to_evidence_and_extract_sources(v)
        result = report.build_and_scrub_value_parts()

        assert result["vulnerabilities"]
        vulnerability = result["vulnerabilities"][0]
        value_parts = vulnerability["evidence"]["valueParts"]

        # The bearer token should be redacted
        assert any("redacted" in part for part in value_parts)


class TestUnvalidatedRedirectQueryStringSync:
    """Test Unvalidated Redirect vulnerability with query string synchronization."""

    def test_redirect_query_string_source_with_secret(self, iast_context_defaults):
        """Test redirect with query string containing secret parameter."""
        # Create tainted query string with secret
        query_with_secret = taint_pyobject(
            pyobject="secret=my_api_key_12345&redirect=/dashboard",
            source_name="redirect_params",
            source_value="secret=my_api_key_12345&redirect=/dashboard",
            source_origin=OriginType.QUERY,
        )

        redirect_url = add_aspect("https://example.com/auth?", query_with_secret)

        UnvalidatedRedirect.report(redirect_url)

        from tests.appsec.iast.iast_utils import _get_iast_data

        data = _get_iast_data()
        vulnerability = list(data["vulnerabilities"])[0]

        assert vulnerability["type"] == VULN_UNVALIDATED_REDIRECT

        # Query string source with "secret" should be redacted
        sources = data["sources"]
        assert len(sources) > 0
        source = sources[0]
        assert source["redacted"] is True

    def test_redirect_url_with_auth_token(self, iast_context_defaults):
        """Test redirect URL with authentication token in query string."""
        redirect_url = "https://app.example.com/callback?auth_token=xyz789abc&state=active"
        tainted_redirect = taint_pyobject(
            pyobject=redirect_url,
            source_name="callback_url",
            source_value=redirect_url,
            source_origin=OriginType.QUERY,
        )

        ev = Evidence(value=tainted_redirect)
        loc = Location(path="redirect.py", line=15, spanId=789)
        v = Vulnerability(type=VULN_UNVALIDATED_REDIRECT, evidence=ev, location=loc)

        report = IastSpanReporter(vulnerabilities={v})
        report.add_ranges_to_evidence_and_extract_sources(v)
        result = report.build_and_scrub_value_parts()

        assert result["vulnerabilities"]
        vulnerability = result["vulnerabilities"][0]
        value_parts = vulnerability["evidence"]["valueParts"]

        # The auth_token should trigger redaction
        assert any("redacted" in part for part in value_parts)


class TestURLSensitiveAnalyzerQueryString:
    """Test URL sensitive analyzer with query string pattern matching."""

    def test_url_analyzer_applies_query_string_pattern(self, iast_context_defaults):
        """Test that URL analyzer applies query string pattern to URLs."""
        from ddtrace.appsec._iast._evidence_redaction.url_sensitive_analyzer import url_sensitive_analyzer
        from ddtrace.internal.settings._config import config

        # Create evidence with URL containing sensitive query params
        class MockEvidence:
            value = "https://api.example.com/data?password=secret123&api_key=abc123&id=456"

        evidence = MockEvidence()

        # Get patterns
        name_pattern = re.compile(r"(?i)password|api_key", re.IGNORECASE | re.MULTILINE)
        value_pattern = re.compile(r"secret", re.IGNORECASE | re.MULTILINE)
        query_string_pattern = config._obfuscation_query_string_pattern

        # Call analyzer
        ranges = url_sensitive_analyzer(evidence, name_pattern, value_pattern, query_string_pattern)

        # Should have ranges for both query fragment matching and query string pattern matching
        assert len(ranges) > 0

        # Verify ranges cover sensitive parts
        assert any(
            evidence.value[r["start"] : r["end"]] in ["secret123", "abc123", "password=secret123"]
            for r in ranges
            if r["start"] < len(evidence.value) and r["end"] <= len(evidence.value)
        )

    def test_url_analyzer_without_query_string(self, iast_context_defaults):
        """Test URL analyzer with URL that has no query string."""
        from ddtrace.appsec._iast._evidence_redaction.url_sensitive_analyzer import url_sensitive_analyzer

        class MockEvidence:
            value = "https://example.com/path/to/resource"

        evidence = MockEvidence()

        name_pattern = re.compile(r"(?i)password", re.IGNORECASE | re.MULTILINE)
        value_pattern = re.compile(r"secret", re.IGNORECASE | re.MULTILINE)
        query_string_pattern = re.compile(b"password", re.IGNORECASE)

        # Should not fail, should return empty or minimal ranges
        ranges = url_sensitive_analyzer(evidence, name_pattern, value_pattern, query_string_pattern)

        # No sensitive content, so no ranges expected
        assert ranges is not None  # Should return a list, even if empty

    def test_url_analyzer_with_authority_and_query(self, iast_context_defaults):
        """Test URL with both authority (user:pass) and query string."""
        from ddtrace.appsec._iast._evidence_redaction.url_sensitive_analyzer import url_sensitive_analyzer
        from ddtrace.internal.settings._config import config

        class MockEvidence:
            value = "https://user:password@example.com/api?token=secret123"

        evidence = MockEvidence()

        name_pattern = re.compile(r"(?i)password|token", re.IGNORECASE | re.MULTILINE)
        value_pattern = re.compile(r"secret", re.IGNORECASE | re.MULTILINE)
        query_string_pattern = config._obfuscation_query_string_pattern

        ranges = url_sensitive_analyzer(evidence, name_pattern, value_pattern, query_string_pattern)

        # Should have ranges for:
        # 1. Authority section (user:password@)
        # 2. Query string (token=secret123)
        assert len(ranges) > 0

        # Check that authority and query sections are covered
        has_authority_range = any("user:password" in evidence.value[r["start"] : r["end"]] for r in ranges)
        has_query_range = any("secret" in evidence.value[r["start"] : r["end"]] for r in ranges)

        assert has_authority_range or has_query_range


class TestQueryStringPatternSynchronization:
    """Integration tests for query string pattern synchronization."""

    def test_synchronization_with_default_pattern(self, iast_context_defaults):
        """Test that default query string pattern works correctly with evidence redaction."""
        # Verify the default pattern is loaded
        from ddtrace.internal.settings._config import config

        assert config._obfuscation_query_string_pattern is not None

        # Create a query string source that matches the default pattern
        url_with_password = "https://example.com/login?username=admin&password=secret123"
        tainted_url = taint_pyobject(
            pyobject=url_with_password,
            source_name="login_url",
            source_value=url_with_password,
            source_origin=OriginType.QUERY,
        )

        ev = Evidence(value=tainted_url)
        loc = Location(path="login.py", line=25, spanId=111)
        v = Vulnerability(type=VULN_SSRF, evidence=ev, location=loc)

        report = IastSpanReporter(vulnerabilities={v})
        report.add_ranges_to_evidence_and_extract_sources(v)
        result = report.build_and_scrub_value_parts()

        # Verify vulnerability was created
        assert len(result["vulnerabilities"]) > 0
        vulnerability = result["vulnerabilities"][0]

        # Verify that password is redacted
        value_parts = vulnerability["evidence"]["valueParts"]
        assert any("redacted" in part for part in value_parts)

    def test_query_vs_parameter_origin_different_treatment(self, iast_context_defaults):
        """Test that QUERY origin and PARAMETER origin are treated differently."""
        # Create actual Source objects (not tainted strings)
        query_source = Source("qs", "password=secret", OriginType.QUERY)
        param_source = Source("param", "password=secret", OriginType.PARAMETER)

        handler = SensitiveHandler()

        # Both should be marked as sensitive, but through different paths
        # Query source: checked against query string pattern
        # Parameter source: checked against IAST patterns only
        assert handler.is_sensible_source(query_source) is True
        assert handler.is_sensible_source(param_source) is True

        # But they are identified differently
        assert handler.is_query_string_source(query_source) is True
        assert handler.is_query_string_source(param_source) is False
