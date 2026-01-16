"""
Hardcoded secret analyzer for IAST.

This module analyzes Python code for hardcoded secrets during AST transformation.
"""

from typing import List
from typing import Optional

from ddtrace.appsec._iast._hardcoded_secrets import NAME_AND_VALUE_RULES
from ddtrace.appsec._iast._hardcoded_secrets import VALUE_ONLY_RULES
from ddtrace.appsec._iast.constants import VULN_HARDCODED_SECRET
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class LiteralInfo:
    """Information about a string literal found in code."""

    def __init__(
        self,
        value: str,
        file: str,
        line: int,
        column: int,
        identifier: Optional[str] = None,
    ):
        self.value = value
        self.file = file
        self.line = line
        self.column = column
        self.identifier = identifier  # Variable name if this is an assignment


class HardcodedSecretAnalyzer:
    """Analyzes code literals for hardcoded secrets."""

    def __init__(self):
        self.value_only_rules = VALUE_ONLY_RULES
        self.name_and_value_rules = NAME_AND_VALUE_RULES

    def analyze_literal(self, literal: LiteralInfo) -> Optional[Vulnerability]:
        """
        Analyze a single literal for hardcoded secrets.

        Args:
            literal: Information about the literal to analyze

        Returns:
            Vulnerability if a secret is detected, None otherwise
        """
        if not literal.value or len(literal.value) < 8:  # Skip very short strings
            return None

        matched_rule = None

        # Try NAME_AND_VALUE rules if we have an identifier
        if literal.identifier:
            full_value = f"{literal.identifier}={literal.value}"
            for rule in self.name_and_value_rules:
                if rule.regex.search(full_value):
                    matched_rule = rule
                    break

        # If no NAME_AND_VALUE match, try VALUE_ONLY rules
        if not matched_rule:
            for rule in self.value_only_rules:
                if rule.regex.search(literal.value):
                    matched_rule = rule
                    break

        if matched_rule:
            return self._create_vulnerability(literal, matched_rule.id)

        return None

    def _create_vulnerability(self, literal: LiteralInfo, rule_id: str) -> Vulnerability:
        """
        Create a vulnerability report for a detected secret.

        Args:
            literal: The literal containing the secret
            rule_id: ID of the matched rule

        Returns:
            Vulnerability object
        """
        evidence = Evidence(value=rule_id)  # Report rule ID instead of the actual secret value

        location = Location(
            spanId=0,  # Will be set when added to reporter
            path=literal.file,
            line=literal.line,
        )

        return Vulnerability(
            type=VULN_HARDCODED_SECRET,
            evidence=evidence,
            location=location,
        )

    def analyze_literals(self, literals: List[LiteralInfo]) -> List[Vulnerability]:
        """
        Analyze multiple literals for hardcoded secrets.

        Args:
            literals: List of literals to analyze

        Returns:
            List of detected vulnerabilities
        """
        vulnerabilities = []

        for literal in literals:
            try:
                vuln = self.analyze_literal(literal)
                if vuln:
                    vulnerabilities.append(vuln)
            except Exception as e:
                log.debug("Error analyzing literal for hardcoded secrets: %s", e)

        return vulnerabilities


# Global analyzer instance
_analyzer = None


def get_hardcoded_secret_analyzer() -> HardcodedSecretAnalyzer:
    """Get or create the global hardcoded secret analyzer instance."""
    global _analyzer
    if _analyzer is None:
        _analyzer = HardcodedSecretAnalyzer()
    return _analyzer


def analyze_string_literal(
    value: str,
    file: str,
    line: int,
    column: int,
    identifier: Optional[str] = None,
) -> Optional[Vulnerability]:
    """
    Analyze a string literal for hardcoded secrets.

    This is a convenience function that can be called from the AST visitor.

    Args:
        value: The string value
        file: File path
        line: Line number
        column: Column number
        identifier: Variable name if this is an assignment

    Returns:
        Vulnerability if a secret is detected, None otherwise
    """
    analyzer = get_hardcoded_secret_analyzer()
    literal = LiteralInfo(
        value=value,
        file=file,
        line=line,
        column=column,
        identifier=identifier,
    )
    return analyzer.analyze_literal(literal)
