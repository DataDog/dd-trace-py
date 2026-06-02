import re

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
AUTHORITY_PATTERN = re.compile(r"https?://([^@]+)(?=@)", re.IGNORECASE | re.MULTILINE)
QUERY_FRAGMENT_PATTERN = re.compile(r"[?#&]([^=&;]+)=([^?#&]+)", re.IGNORECASE | re.MULTILINE)


def find_authority(ranges, evidence):
    regex_result = AUTHORITY_PATTERN.search(evidence.value)
    while regex_result is not None:
        if isinstance(regex_result.group(1), str):
            start = regex_result.start(1)
            end = regex_result.start(1) + (len(regex_result.group(1)))
            ranges.append({"start": start, "end": end})

        regex_result = AUTHORITY_PATTERN.search(evidence.value, regex_result.end())


def find_query_fragment(ranges, evidence):
    regex_result = QUERY_FRAGMENT_PATTERN.search(evidence.value)
    while regex_result is not None:
        if isinstance(regex_result.group(2), str):
            start = regex_result.start(2)
            end = regex_result.start(2) + (len(regex_result.group(2)))
            ranges.append({"start": start, "end": end})
        regex_result = QUERY_FRAGMENT_PATTERN.search(evidence.value, regex_result.end())


def find_query_string_matches(ranges, evidence, query_string_pattern):
    """
    Find sensitive data in query string using the query string obfuscation pattern.
    This ensures synchronization with span-level query string redaction.
    """
    if query_string_pattern is None:
        return

    try:
        # Extract query string portion from URL
        if "?" not in evidence.value:
            return

        # Find the query string part
        query_start = evidence.value.find("?")
        query_end = evidence.value.find("#") if "#" in evidence.value else len(evidence.value)
        query_string = evidence.value[query_start:query_end]

        # Convert to bytes for pattern matching (query string pattern is in bytes)
        query_bytes = query_string if isinstance(query_string, bytes) else query_string.encode("utf-8")

        # Find all matches
        for match in query_string_pattern.finditer(query_bytes):
            start = query_start + match.start()
            end = query_start + match.end()
            ranges.append({"start": start, "end": end})
    except Exception:
        log.debug("Error applying query string pattern to URL evidence", exc_info=True)


def url_sensitive_analyzer(evidence, name_pattern=None, value_pattern=None, query_string_pattern=None):
    """
    Analyzes URL evidence for sensitive information.

    Args:
    - evidence: The evidence to analyze
    - name_pattern: Pattern for matching sensitive names
    - value_pattern: Pattern for matching sensitive values
    - query_string_pattern: Pattern for matching sensitive query strings (for synchronization)

    Returns:
    - list: List of sensitive ranges to redact
    """
    ranges = []
    find_authority(ranges, evidence)
    find_query_fragment(ranges, evidence)
    # Apply query string pattern for synchronization with span-level redaction
    find_query_string_matches(ranges, evidence, query_string_pattern)
    return ranges
