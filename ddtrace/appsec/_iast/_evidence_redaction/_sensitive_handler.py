import re
import string

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings.asm import config as asm_config

from .._taint_tracking import OriginType
from .._utils import _get_source_index
from ..constants import MAX_REDACTION_VALUE_LENGTH
from ..constants import VULN_CMDI
from ..constants import VULN_CODE_INJECTION
from ..constants import VULN_HEADER_INJECTION
from ..constants import VULN_SQL_INJECTION
from ..constants import VULN_SSRF
from ..constants import VULN_UNTRUSTED_SERIALIZATION
from ..constants import VULN_UNVALIDATED_REDIRECT
from ..constants import VULN_XSS
from .command_injection_sensitive_analyzer import command_injection_sensitive_analyzer
from .default_sensitive_analyzer import default_sensitive_analyzer
from .header_injection_sensitive_analyzer import header_injection_sensitive_analyzer
from .sql_sensitive_analyzer import sql_sensitive_analyzer
from .url_sensitive_analyzer import url_sensitive_analyzer


log = get_logger(__name__)

REDACTED_SOURCE_BUFFER = string.ascii_letters + string.digits
LEN_SOURCE_BUFFER = len(REDACTED_SOURCE_BUFFER)
VALUE_MAX_LENGHT = 45


def get_redacted_source(length):
    full_repeats = length // LEN_SOURCE_BUFFER
    remainder = length % LEN_SOURCE_BUFFER
    result = REDACTED_SOURCE_BUFFER * full_repeats + REDACTED_SOURCE_BUFFER[:remainder]
    return result


class SensitiveHandler:
    """
    Class responsible for handling sensitive information.
    """

    def __init__(self):
        self._name_pattern = re.compile(asm_config._iast_redaction_name_pattern, re.IGNORECASE | re.MULTILINE)
        self._value_pattern = re.compile(asm_config._iast_redaction_value_pattern, re.IGNORECASE | re.MULTILINE)
        # Query string obfuscation pattern for synchronization with span-level redaction
        self._query_string_pattern = config._obfuscation_query_string_pattern

        self._sensitive_analyzers = {
            VULN_CMDI: command_injection_sensitive_analyzer,
            VULN_SQL_INJECTION: sql_sensitive_analyzer,
            VULN_SSRF: url_sensitive_analyzer,
            VULN_UNVALIDATED_REDIRECT: url_sensitive_analyzer,
            VULN_HEADER_INJECTION: header_injection_sensitive_analyzer,
            VULN_XSS: default_sensitive_analyzer,
            VULN_CODE_INJECTION: default_sensitive_analyzer,
            VULN_UNTRUSTED_SERIALIZATION: default_sensitive_analyzer,
        }

    def is_sensible_name(self, name):
        """
        Checks if a name is sensible based on the name pattern.

        Args:
        - name (str): The name to check.

        Returns:
        - bool: True if the name is sensible, False otherwise.
        """
        return bool(self._name_pattern.search(name))

    def is_sensible_value(self, value):
        """
        Checks if a value is sensible based on the value pattern.

        Args:
        - value (str): The value to check.

        Returns:
        - bool: True if the value is sensible, False otherwise.
        """
        return len(value) > MAX_REDACTION_VALUE_LENGTH or bool(self._value_pattern.search(value))

    def is_query_string_source(self, source):
        """
        Checks if a source originates from a query string.

        Args:
        - source: The source to check.

        Returns:
        - bool: True if the source is from a query string, False otherwise.
        """
        try:
            return source is not None and hasattr(source, "origin") and source.origin == OriginType.QUERY
        except Exception:
            return False

    def is_sensible_source(self, source):
        """
        Checks if a source is sensible.

        Args:
        - source (dict): The source to check.

        Returns:
        - bool: True if the source is sensible, False otherwise.
        """
        if source is None or source.value is None:
            return False

        # For query string sources, check against the query string obfuscation pattern
        # to maintain synchronization with span-level redaction
        if self.is_query_string_source(source) and self._query_string_pattern is not None:
            try:
                # Convert pattern to string for matching (pattern is in bytes, source value is string)
                value_bytes = source.value if isinstance(source.value, bytes) else source.value.encode("utf-8")
                if self._query_string_pattern.search(value_bytes):
                    return True
            except Exception:
                log.debug("Error checking query string pattern against source", exc_info=True)

        # Standard IAST redaction patterns
        return self.is_sensible_name(source.name) or self.is_sensible_value(source.value)

    def scrub_evidence(self, vulnerability_type, evidence, tainted_ranges, sources):
        """
        Scrubs evidence based on the given vulnerability type.

        Args:
        - vulnerability_type (str): The vulnerability type.
        - evidence (dict): The evidence to scrub.
        - tainted_ranges (list): List of tainted ranges.
        - sources (list): List of sources.

        Returns:
        - dict: The scrubbed evidence.
        """
        if asm_config._iast_redaction_enabled:
            sensitive_analyzer = self._sensitive_analyzers.get(vulnerability_type)
            if sensitive_analyzer:
                if not evidence.value:
                    log.debug("No evidence value found in evidence %s", evidence)
                    return None
                # Pass query string pattern for synchronization with span-level redaction
                sensitive_ranges = sensitive_analyzer(
                    evidence, self._name_pattern, self._value_pattern, self._query_string_pattern
                )
                return self.to_redacted_json(evidence.value, sensitive_ranges, tainted_ranges, sources)
        return None

    @staticmethod
    def _normalize_sensitive_ranges(ranges, max_end):
        non_empty = []
        empty_positions = set()
        for r in ranges:
            start, end = r["start"], r["end"]
            if start > end or end < 0 or start > max_end:
                continue
            if start == end:
                empty_positions.add(start)
            else:
                clipped_start = max(0, start)
                clipped_end = min(max_end, end)
                if clipped_start < clipped_end:
                    non_empty.append((clipped_start, clipped_end))
        non_empty.sort()
        merged = []
        for start, end in non_empty:
            if merged and start <= merged[-1][1]:
                if end > merged[-1][1]:
                    merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
        empty_markers = []
        m_idx = 0
        for pos in sorted(empty_positions):
            while m_idx < len(merged) and merged[m_idx][1] < pos:
                m_idx += 1
            if m_idx < len(merged) and merged[m_idx][0] <= pos <= merged[m_idx][1]:
                continue
            empty_markers.append(pos)
        return merged, empty_markers

    @staticmethod
    def _sensitive_slices(seg_start, seg_end, sensitive, empty_markers):
        slices = [(max(s, seg_start), min(e, seg_end)) for s, e in sensitive if s < seg_end and e > seg_start]
        for pos in empty_markers:
            if seg_start <= pos < seg_end:
                slices.append((pos, pos))
        if len(slices) > 1:
            slices.sort()
        return slices

    @staticmethod
    def _mark_source_redacted(source_index, source, redacted_sources):
        if source_index not in redacted_sources:
            redacted_sources.append(source_index)
        if not source.redacted:
            source.pattern = get_redacted_source(len(source.value)) if source.value else ""
            source.redacted = True

    @staticmethod
    def _redacted_pattern_for_chunk(chunk, source):
        idx = source.value.find(chunk) if source.value is not None else -1
        if idx > -1 and source.pattern and idx + len(chunk) <= len(source.pattern):
            return source.pattern[idx : idx + len(chunk)]
        return "*" * len(chunk)

    def _emit_untainted_segment(self, value_parts, evidence_value, seg_start, seg_end, slices):
        pos = seg_start
        for s_start, s_end in slices:
            if s_start > pos:
                self.write_value_part(value_parts, evidence_value[pos:s_start])
            value_parts.append({"redacted": True})
            pos = s_end
        if pos < seg_end:
            self.write_value_part(value_parts, evidence_value[pos:seg_end])

    def to_redacted_json(self, evidence_value, sensitive, tainted_ranges, sources):
        """
        Converts evidence value to redacted JSON format.

        Args:
        - evidence_value (str): The evidence value.
        - sensitive (list): List of sensitive ranges.
        - tainted_ranges (list): List of tainted ranges.
        - sources (list): List of sources.

        Returns:
        - dict: The redacted JSON.
        """
        value_parts = []
        redacted_sources = []
        length = len(evidence_value)
        merged_sensitive, empty_markers = self._normalize_sensitive_ranges(sensitive, length)

        normalized_tainted = sorted(
            (
                (max(0, tr["start"]), min(length, tr["end"]), tr["source"])
                for tr in (tainted_ranges or [])
                if tr["start"] < tr["end"] and tr["end"] > 0 and tr["start"] < length
            ),
            key=lambda item: (item[0], item[1]),
        )

        cursor = 0
        for t_start, t_end, taint_source in normalized_tainted:
            t_start = max(t_start, cursor)
            if t_start >= t_end:
                continue

            if t_start > cursor:
                gap_slices = self._sensitive_slices(cursor, t_start, merged_sensitive, empty_markers)
                self._emit_untainted_segment(value_parts, evidence_value, cursor, t_start, gap_slices)

            tainted_slices = self._sensitive_slices(t_start, t_end, merged_sensitive, empty_markers)
            source_index = _get_source_index(sources, taint_source)
            if not (0 <= source_index < len(sources)):
                self._emit_untainted_segment(value_parts, evidence_value, t_start, t_end, tainted_slices)
            else:
                source = sources[source_index]
                if self.is_sensible_source(source):
                    self._mark_source_redacted(source_index, source, redacted_sources)
                    part_value = evidence_value[t_start:t_end]
                    pattern = (
                        source.pattern
                        if part_value and source.value is not None and part_value in source.value
                        else "*" * len(part_value)
                    )
                    value_parts.append({"redacted": True, "source": source_index, "pattern": pattern})
                else:
                    if any(s_start < s_end for s_start, s_end in tainted_slices):
                        self._mark_source_redacted(source_index, source, redacted_sources)
                    pos = t_start
                    for s_start, s_end in tainted_slices:
                        if s_start > pos:
                            value_parts.append({"value": evidence_value[pos:s_start], "source": source_index})
                        if s_start == s_end:
                            value_parts.append({"redacted": True})
                        else:
                            pattern = self._redacted_pattern_for_chunk(evidence_value[s_start:s_end], source)
                            value_parts.append({"redacted": True, "source": source_index, "pattern": pattern})
                        pos = s_end
                    if pos < t_end:
                        value_parts.append({"value": evidence_value[pos:t_end], "source": source_index})

            cursor = t_end

        tail_slices = self._sensitive_slices(cursor, length, merged_sensitive, empty_markers)
        self._emit_untainted_segment(value_parts, evidence_value, cursor, length, tail_slices)

        return {"redacted_value_parts": value_parts, "redacted_sources": redacted_sources}

    def write_value_part(self, value_parts, value, source_index=None):
        if value:
            if source_index is not None:
                value_parts.append({"value": value, "source": source_index})
            elif len(value) < VALUE_MAX_LENGHT:
                value_parts.append({"value": value})
            else:
                value_parts.append({"redacted": True})

    def set_redaction_patterns(self, redaction_name_pattern=None, redaction_value_pattern=None):
        if redaction_name_pattern:
            try:
                self._name_pattern = re.compile(redaction_name_pattern, re.IGNORECASE | re.MULTILINE)
            except re.error:
                log.warning("Redaction name pattern is not valid")

        if redaction_value_pattern:
            try:
                self._value_pattern = re.compile(redaction_value_pattern, re.IGNORECASE | re.MULTILINE)
            except re.error:
                log.warning("Redaction value pattern is not valid")


sensitive_handler = SensitiveHandler()
