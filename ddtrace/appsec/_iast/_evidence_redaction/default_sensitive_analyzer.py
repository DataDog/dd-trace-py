from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def default_sensitive_analyzer(evidence, name_pattern, value_pattern, query_string_pattern=None):
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
    if name_pattern.search(evidence.value) or value_pattern.search(evidence.value):
        return [{"start": 0, "end": len(evidence.value)}]

    return []
