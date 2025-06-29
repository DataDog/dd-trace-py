import re
import string

from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from .._utils import _get_source_index
from ..constants import VULN_CMDI
from ..constants import VULN_CODE_INJECTION
from ..constants import VULN_HEADER_INJECTION
from ..constants import VULN_SQL_INJECTION
from ..constants import VULN_SSRF
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

        self._sensitive_analyzers = {
            VULN_CMDI: command_injection_sensitive_analyzer,
            VULN_SQL_INJECTION: sql_sensitive_analyzer,
            VULN_SSRF: url_sensitive_analyzer,
            VULN_UNVALIDATED_REDIRECT: url_sensitive_analyzer,
            VULN_HEADER_INJECTION: header_injection_sensitive_analyzer,
            VULN_XSS: default_sensitive_analyzer,
            VULN_CODE_INJECTION: default_sensitive_analyzer,
        }

    @staticmethod
    def _contains(range_container, range_contained):
        """
        Checks if a range_container contains another range_contained.

        Args:
        - range_container (dict): The container range.
        - range_contained (dict): The contained range.

        Returns:
        - bool: True if range_container contains range_contained, False otherwise.
        """
        if range_container["start"] > range_contained["start"]:
            return False
        return range_container["end"] >= range_contained["end"]

    @staticmethod
    def _intersects(range_a, range_b):
        """
        Checks if two ranges intersect.

        Args:
        - range_a (dict): First range.
        - range_b (dict): Second range.

        Returns:
        - bool: True if the ranges intersect, False otherwise.
        """
        return range_b["start"] < range_a["end"] and range_b["end"] > range_a["start"]

    def _remove(self, range_, range_to_remove):
        """
        Removes a range_to_remove from a range_.

        Args:
        - range_ (dict): The range to remove from.
        - range_to_remove (dict): The range to remove.

        Returns:
        - list: List containing the remaining parts after removing the range_to_remove.
        """
        if not self._intersects(range_, range_to_remove):
            return [range_]
        elif self._contains(range_to_remove, range_):
            return []
        else:
            result = []
            if range_to_remove["start"] > range_["start"]:
                offset = range_to_remove["start"] - range_["start"]
                result.append({"start": range_["start"], "end": range_["start"] + offset})
            if range_to_remove["end"] < range_["end"]:
                offset = range_["end"] - range_to_remove["end"]
                result.append({"start": range_to_remove["end"], "end": range_to_remove["end"] + offset})
            return result

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
        return bool(self._value_pattern.search(value))

    def is_sensible_source(self, source):
        """
        Checks if a source is sensible.

        Args:
        - source (dict): The source to check.

        Returns:
        - bool: True if the source is sensible, False otherwise.
        """
        return (
            source is not None
            and source.value is not None
            and (self.is_sensible_name(source.name) or self.is_sensible_value(source.value))
        )

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
                sensitive_ranges = sensitive_analyzer(evidence, self._name_pattern, self._value_pattern)
                return self.to_redacted_json(evidence.value, sensitive_ranges, tainted_ranges, sources)
        return None

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
        redacted_sources_context = dict()

        start = 0
        next_tainted_index = 0
        source_index = None

        next_tainted = tainted_ranges.pop(0) if tainted_ranges else None
        next_sensitive = sensitive.pop(0) if sensitive else None
        i = 0
        while i < len(evidence_value):
            if next_tainted and next_tainted["start"] == i:
                self.write_value_part(value_parts, evidence_value[start:i], source_index)

                source_index = _get_source_index(sources, next_tainted["source"])

                while next_sensitive and self._contains(next_tainted, next_sensitive):
                    redaction_start = next_sensitive["start"] - next_tainted["start"]
                    redaction_end = next_sensitive["end"] - next_tainted["start"]
                    if redaction_start == redaction_end:
                        self.write_redacted_value_part(value_parts, 0)
                    else:
                        self.redact_source(
                            sources,
                            redacted_sources,
                            redacted_sources_context,
                            source_index,
                            redaction_start,
                            redaction_end,
                        )
                    next_sensitive = sensitive.pop(0) if sensitive else None

                if next_sensitive and self._intersects(next_sensitive, next_tainted):
                    redaction_start = next_sensitive["start"] - next_tainted["start"]
                    redaction_end = next_sensitive["end"] - next_tainted["start"]

                    self.redact_source(
                        sources,
                        redacted_sources,
                        redacted_sources_context,
                        source_index,
                        redaction_start,
                        redaction_end,
                    )

                    entries = self._remove(next_sensitive, next_tainted)
                    next_sensitive = entries[0] if entries else None

                if source_index < len(sources):
                    if not sources[source_index].redacted and self.is_sensible_source(sources[source_index]):
                        redacted_sources.append(source_index)
                        sources[source_index].pattern = get_redacted_source(len(sources[source_index].value))
                        sources[source_index].redacted = True

                if source_index in redacted_sources:
                    part_value = evidence_value[i : i + (next_tainted["end"] - next_tainted["start"])]

                    self.write_redacted_value_part(
                        value_parts,
                        len(part_value),
                        source_index,
                        part_value,
                        sources[source_index],
                        redacted_sources_context.get(source_index),
                        self.is_sensible_source(sources[source_index]),
                    )
                    redacted_sources_context[source_index] = []
                else:
                    substring_end = min(next_tainted["end"], len(evidence_value))
                    self.write_value_part(
                        value_parts, evidence_value[next_tainted["start"] : substring_end], source_index
                    )

                start = i + (next_tainted["end"] - next_tainted["start"])
                i = start - 1
                next_tainted = tainted_ranges.pop(0) if tainted_ranges else None
                next_tainted_index += 1
                source_index = None
                continue
            elif next_sensitive and next_sensitive["start"] == i:
                self.write_value_part(value_parts, evidence_value[start:i], source_index)
                if next_tainted and self._intersects(next_sensitive, next_tainted):
                    source_index = next_tainted_index

                    redaction_start = next_sensitive["start"] - next_tainted["start"]
                    redaction_end = next_sensitive["end"] - next_tainted["start"]
                    self.redact_source(
                        sources,
                        redacted_sources,
                        redacted_sources_context,
                        next_tainted_index,
                        redaction_start,
                        redaction_end,
                    )

                    entries = self._remove(next_sensitive, next_tainted)
                    next_sensitive = entries[0] if entries else None

                length = next_sensitive["end"] - next_sensitive["start"]
                self.write_redacted_value_part(value_parts, length)

                start = i + length
                i = start - 1
                next_sensitive = sensitive.pop(0) if sensitive else None
                continue
            i += 1
        if start < len(evidence_value):
            self.write_value_part(value_parts, evidence_value[start:])

        return {"redacted_value_parts": value_parts, "redacted_sources": redacted_sources}

    def redact_source(self, sources, redacted_sources, redacted_sources_context, source_index, start, end):
        if source_index is not None and source_index < len(sources):
            if not sources[source_index].redacted:
                redacted_sources.append(source_index)
                sources[source_index].pattern = get_redacted_source(len(sources[source_index].value))
                sources[source_index].redacted = True

            if source_index not in redacted_sources_context.keys():
                redacted_sources_context[source_index] = []

            redacted_sources_context[source_index].append({"start": start, "end": end})

    def write_value_part(self, value_parts, value, source_index=None):
        if value:
            if source_index is not None:
                value_parts.append({"value": value, "source": source_index})
            elif len(value) < VALUE_MAX_LENGHT:
                value_parts.append({"value": value})
            else:
                value_parts.append({"redacted": True})

    def write_redacted_value_part(
        self,
        value_parts,
        length,
        source_index=None,
        part_value=None,
        source=None,
        source_redaction_context=None,
        is_sensible_source=False,
    ):
        if source_index is not None:
            placeholder = source.pattern if part_value and part_value in source.value else "*" * length

            if is_sensible_source:
                value_parts.append({"redacted": True, "source": source_index, "pattern": placeholder})
            else:
                _value = part_value
                deduped_source_redaction_contexts = []

                for _source_redaction_context in source_redaction_context:
                    if _source_redaction_context not in deduped_source_redaction_contexts:
                        deduped_source_redaction_contexts.append(_source_redaction_context)

                offset = 0
                for _source_redaction_context in deduped_source_redaction_contexts:
                    if _source_redaction_context["start"] > 0:
                        value_parts.append(
                            {"source": source_index, "value": _value[: _source_redaction_context["start"] - offset]}
                        )
                        _value = _value[_source_redaction_context["start"] - offset :]
                        offset = _source_redaction_context["start"]

                    sensitive_start = _source_redaction_context["start"] - offset
                    if sensitive_start < 0:
                        sensitive_start = 0
                    sensitive = _value[sensitive_start : _source_redaction_context["end"] - offset]
                    index_of_part_value_in_pattern = source.value.find(sensitive)

                    pattern = (
                        placeholder[index_of_part_value_in_pattern : index_of_part_value_in_pattern + len(sensitive)]
                        if index_of_part_value_in_pattern > -1
                        else placeholder[_source_redaction_context["start"] : _source_redaction_context["end"]]
                    )
                    value_parts.append({"redacted": True, "source": source_index, "pattern": pattern})
                    _value = _value[len(pattern) :]
                    offset += len(pattern)
                if _value:
                    value_parts.append({"source": source_index, "value": _value})

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
