from typing import Optional
from typing import Pattern

from ddtrace.appsec._iast.constants import HEADER_NAME_VALUE_SEPARATOR
from ddtrace.internal.logger import get_logger

from ._types import EvidenceLike
from ._types import SensitiveRange


log = get_logger(__name__)


def header_injection_sensitive_analyzer(
    evidence: EvidenceLike,
    name_pattern: Optional[Pattern[str]],
    value_pattern: Optional[Pattern[str]],
    query_string_pattern: Optional[Pattern[bytes]] = None,
) -> list[SensitiveRange]:
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
    if evidence_value is None or name_pattern is None or value_pattern is None:
        return []
    sections = evidence_value.split(HEADER_NAME_VALUE_SEPARATOR)
    header_name = sections[0]
    header_value = HEADER_NAME_VALUE_SEPARATOR.join(sections[1:])

    if name_pattern.search(header_name) or value_pattern.search(header_value):
        return [{"start": len(header_name) + len(HEADER_NAME_VALUE_SEPARATOR), "end": len(evidence_value)}]

    return []
