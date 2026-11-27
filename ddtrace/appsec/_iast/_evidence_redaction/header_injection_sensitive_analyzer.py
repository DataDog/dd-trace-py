import re
from typing import Optional

from ddtrace.appsec._iast._report_types import Evidence
from ddtrace.appsec._iast.constants import HEADER_NAME_VALUE_SEPARATOR
from ddtrace.appsec._iast.constants import MAX_REDACTION_VALUE_LENGTH
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def header_injection_sensitive_analyzer(
    evidence: Evidence,
    name_pattern: re.Pattern[str],
    value_pattern: re.Pattern[str],
    query_string_pattern: Optional[re.Pattern[bytes]] = None,
) -> list[dict[str, int]]:
    """
    Header injection sensitive analyzer for evidence redaction.

    Args:
    - evidence: The evidence to analyze
    - name_pattern: Pattern for matching sensitive names
    - value_pattern: Pattern for matching sensitive values
    - query_string_pattern: Query string obfuscation pattern (unused in header injection analyzer)

    Returns:
    - list: List of sensitive ranges to redact
    """
    evidence_value = evidence.value
    if evidence_value is None:
        return []

    header_name, separator, header_value = evidence_value.partition(HEADER_NAME_VALUE_SEPARATOR)
    if not separator or not header_value:
        return []

    if (
        len(header_value) > MAX_REDACTION_VALUE_LENGTH
        or name_pattern.search(header_name)
        or value_pattern.search(header_value)
    ):
        return [{"start": len(header_name) + len(HEADER_NAME_VALUE_SEPARATOR), "end": len(evidence_value)}]

    return []
