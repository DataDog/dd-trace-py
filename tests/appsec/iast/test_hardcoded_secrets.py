"""
Tests for hardcoded secret detection in IAST.
"""

from ddtrace.appsec._iast._hardcoded_secret_analyzer import analyze_string_literal
from ddtrace.appsec._iast.constants import VULN_HARDCODED_SECRET


class TestHardcodedSecretDetection:
    """Test hardcoded secret detection functionality."""

    def test_detect_aws_access_key(self):
        """Test detection of AWS access key."""
        aws_key = "AKIAIOSFODNN7EXAMPLE"
        vuln = analyze_string_literal(
            value=aws_key,
            file="test.py",
            line=10,
            column=0,
            identifier="aws_access_key",
        )

        assert vuln is not None
        assert vuln.type == VULN_HARDCODED_SECRET
        assert vuln.evidence.value == "aws-access-token"
        assert vuln.location.line == 10
        assert vuln.location.path == "test.py"

    def test_detect_github_pat(self):
        """Test detection of GitHub Personal Access Token."""
        github_pat = "ghp_1234567890abcdefghijklmnopqrstuv"
        vuln = analyze_string_literal(
            value=github_pat,
            file="config.py",
            line=5,
            column=0,
            identifier="github_token",
        )

        assert vuln is not None
        assert vuln.type == VULN_HARDCODED_SECRET
        assert vuln.evidence.value == "github-pat"

    def test_detect_jwt_token(self):
        """Test detection of JWT token."""
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        vuln = analyze_string_literal(
            value=jwt,
            file="auth.py",
            line=20,
            column=4,
            identifier="token",
        )

        assert vuln is not None
        assert vuln.type == VULN_HARDCODED_SECRET
        assert vuln.evidence.value == "jwt"

    def test_no_detection_for_short_strings(self):
        """Test that short strings are not detected as secrets."""
        short_string = "hello"
        vuln = analyze_string_literal(
            value=short_string,
            file="test.py",
            line=1,
            column=0,
            identifier="greeting",
        )

        assert vuln is None

    def test_no_detection_for_normal_strings(self):
        """Test that normal strings are not detected as secrets."""
        normal_string = "This is a normal string with no secrets"
        vuln = analyze_string_literal(
            value=normal_string,
            file="test.py",
            line=1,
            column=0,
            identifier="message",
        )

        assert vuln is None

    def test_detect_stripe_key(self):
        """Test detection of Stripe API key."""
        stripe_key = "sk_test_1234567890abcdefghijklmnop"
        vuln = analyze_string_literal(
            value=stripe_key,
            file="payment.py",
            line=15,
            column=0,
            identifier="stripe_key",
        )

        assert vuln is not None
        assert vuln.type == VULN_HARDCODED_SECRET
        assert vuln.evidence.value == "stripe-access-token"

    def test_name_and_value_rule(self):
        """Test NAME_AND_VALUE rule type (requires both identifier and value pattern)."""
        # This should match because it has 'datadog' in the identifier
        datadog_token = "a" * 40  # 40 lowercase alphanumeric chars
        vuln = analyze_string_literal(
            value=datadog_token,
            file="config.py",
            line=8,
            column=0,
            identifier="datadog_api_key",
        )

        # This matches the pattern for datadog-access-token
        assert vuln is not None
        assert vuln.type == VULN_HARDCODED_SECRET
