import re
from typing import Optional
from typing import Union

from ddtrace.appsec._iast._report_types import Evidence
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config


log = get_logger(__name__)
AUTHORITY_PATTERN = re.compile(r"(?<![a-z0-9+.\-])[a-z][a-z0-9+.\-]*://([^@/?#]+)(?=@)", re.IGNORECASE)
QUERY_FRAGMENT_PATTERN = re.compile(r"[?#&]([^=&;?#]+)=([^?#&]+)", re.IGNORECASE)


def find_authority(ranges: list[dict[str, int]], evidence: Evidence) -> None:
    if evidence.value is None:
        return
    regex_result = AUTHORITY_PATTERN.search(evidence.value)
    while regex_result is not None:
        if isinstance(regex_result.group(1), str):
            start = regex_result.start(1)
            end = regex_result.start(1) + (len(regex_result.group(1)))
            ranges.append({"start": start, "end": end})

        regex_result = AUTHORITY_PATTERN.search(evidence.value, regex_result.end())


def find_query_fragment(ranges: list[dict[str, int]], evidence: Evidence) -> None:
    if evidence.value is None:
        return
    regex_result = QUERY_FRAGMENT_PATTERN.search(evidence.value)
    while regex_result is not None:
        if isinstance(regex_result.group(2), str):
            start = regex_result.start(2)
            end = regex_result.start(2) + (len(regex_result.group(2)))
            ranges.append({"start": start, "end": end})
        regex_result = QUERY_FRAGMENT_PATTERN.search(evidence.value, regex_result.end())


def find_query_string_matches(
    ranges: list[dict[str, int]], evidence: Evidence, query_string_pattern: Optional[re.Pattern[bytes]]
) -> None:
    """
    Find sensitive data in query string using the query string obfuscation pattern.
    This ensures synchronization with span-level query string redaction.
    """
    if query_string_pattern is None or evidence.value is None:
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
        query_bytes = query_string.encode("utf-8")
        byte_to_char: Union[range, list[int]] = range(len(query_bytes) + 1)
        if isinstance(query_string, str) and not query_string.isascii():
            # Build once so converting all match offsets takes linear time.
            byte_to_char = [index for index, char in enumerate(query_string) for _ in char.encode("utf-8")]
            byte_to_char.append(len(query_string))

        # Find all matches
        preserve_delimiter = (
            config._query_string_obfuscation_preserve_delimiter
            and query_string_pattern is config._obfuscation_query_string_pattern
        )
        for match in query_string_pattern.finditer(query_bytes):
            start = match.end(1) if preserve_delimiter and match.start(1) != -1 else match.start()
            end = match.end()
            ranges.append(
                {
                    "start": query_start + byte_to_char[start],
                    "end": query_start + (byte_to_char[end - 1] + 1 if end > start else byte_to_char[end]),
                }
            )
    except Exception:
        log.debug("Error applying query string pattern to URL evidence", exc_info=True)


def _merge_ranges(ranges: list[dict[str, int]]) -> list[dict[str, int]]:
    if len(ranges) <= 1:
        return ranges
    ranges.sort(key=lambda r: (r["start"], r["end"]))
    merged = [ranges[0]]
    for current in ranges[1:]:
        last = merged[-1]
        if current["start"] <= last["end"]:
            if current["end"] > last["end"]:
                last["end"] = current["end"]
        else:
            merged.append(current)
    return merged


def url_sensitive_analyzer(
    evidence: Evidence,
    name_pattern: Optional[re.Pattern[str]] = None,
    value_pattern: Optional[re.Pattern[str]] = None,
    query_string_pattern: Optional[re.Pattern[bytes]] = None,
) -> list[dict[str, int]]:
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
    ranges: list[dict[str, int]] = []
    find_authority(ranges, evidence)
    find_query_fragment(ranges, evidence)
    # Apply query string pattern for synchronization with span-level redaction
    find_query_string_matches(ranges, evidence, query_string_pattern)
    return _merge_ranges(ranges)
