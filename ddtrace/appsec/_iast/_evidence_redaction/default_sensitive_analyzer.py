from typing import Optional
from typing import Pattern

from ddtrace.internal.logger import get_logger

from ._types import EvidenceLike
from ._types import SensitiveRange


log = get_logger(__name__)


def default_sensitive_analyzer(
    evidence: EvidenceLike,
    name_pattern: Optional[Pattern[str]],
    value_pattern: Optional[Pattern[str]],
    query_string_pattern: Optional[Pattern[bytes]] = None,
) -> list[SensitiveRange]:
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
    if evidence.value is None or name_pattern is None or value_pattern is None:
        return []
    if name_pattern.search(evidence.value) or value_pattern.search(evidence.value):
        return [{"start": 0, "end": len(evidence.value)}]

    return []
