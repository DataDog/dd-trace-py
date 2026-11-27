import re
from typing import Optional

from ddtrace.appsec._iast._report_types import Evidence
from ddtrace.appsec._iast.constants import MAX_REDACTION_VALUE_LENGTH
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def default_sensitive_analyzer(
    evidence: Evidence,
    name_pattern: re.Pattern[str],
    value_pattern: re.Pattern[str],
    query_string_pattern: Optional[re.Pattern[bytes]] = None,
) -> list[dict[str, int]]:
    """
    Default sensitive analyzer for evidence redaction.

    Args:
    - evidence: The evidence to analyze
    - name_pattern: Pattern for matching sensitive names
    - value_pattern: Pattern for matching sensitive values
    - query_string_pattern: Query string obfuscation pattern (unused in default analyzer)

    Returns:
    - list: List of sensitive ranges to redact
    """
    value = evidence.value
    if value is None:
        return []

    if len(value) > MAX_REDACTION_VALUE_LENGTH or name_pattern.search(value) or value_pattern.search(value):
        return [{"start": 0, "end": len(value)}]

    return []
