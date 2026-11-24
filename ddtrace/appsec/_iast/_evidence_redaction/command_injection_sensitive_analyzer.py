import re
from typing import Optional

from ddtrace.appsec._iast._report_types import Evidence
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_INSIDE_QUOTES_REGEXP = re.compile(r"^(?:\s*(?:sudo|doas)\s+)?\b\S+\b\s*(.*)")
COMMAND_PATTERN = r"^(?:\s*(?:sudo|doas)\s+)?\b\S+\b\s(.*)"
pattern = re.compile(COMMAND_PATTERN, re.IGNORECASE | re.MULTILINE)


def command_injection_sensitive_analyzer(
    evidence: Evidence,
    name_pattern: Optional[re.Pattern[str]] = None,
    value_pattern: Optional[re.Pattern[str]] = None,
    query_string_pattern: Optional[re.Pattern[bytes]] = None,
) -> list[dict[str, int]]:
    """
    Command injection sensitive analyzer for evidence redaction.

    Args:
    - evidence: The evidence to analyze
    - name_pattern: Pattern for matching sensitive names (unused in command injection analyzer)
    - value_pattern: Pattern for matching sensitive values (unused in command injection analyzer)
    - query_string_pattern: Query string obfuscation pattern (unused in command injection analyzer)

    Returns:
    - list: List of sensitive ranges to redact
    """
    if evidence.value is None:
        return []

    regex_result = pattern.search(evidence.value)
    if regex_result and len(regex_result.groups()) > 0:
        start = regex_result.start(1)
        end = regex_result.end(1)
        return [{"start": start, "end": end}]
    return []
