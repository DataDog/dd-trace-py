import dataclasses
from functools import reduce
import json
import operator
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
import zlib

from ddtrace.appsec._constants import STACK_TRACE
from ddtrace.appsec._exploit_prevention.stack_traces import report_stack
from ddtrace.appsec._iast._evidence_redaction._sensitive_handler import sensitive_handler
from ddtrace.appsec._iast._utils import _get_source_index
from ddtrace.appsec._iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec._iast.constants import VULN_WEAK_CIPHER_TYPE
from ddtrace.appsec._iast.constants import VULN_WEAK_RANDOMNESS
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

ATTRS_TO_SKIP = frozenset({"_ranges", "_evidences_with_no_sources", "dialect"})
EVIDENCES_WITH_NO_SOURCES = [VULN_INSECURE_HASHING_TYPE, VULN_WEAK_CIPHER_TYPE, VULN_WEAK_RANDOMNESS]


class NotNoneDictable:
    def _to_dict(self):
        return dataclasses.asdict(
            self,
            dict_factory=lambda x: {k: v for k, v in x if v is not None and k not in ATTRS_TO_SKIP},
        )


@dataclasses.dataclass(eq=False)
class Evidence(NotNoneDictable):
    dialect: Optional[str] = None
    value: Optional[str] = None
    _ranges: List[Dict] = dataclasses.field(default_factory=list)
    valueParts: Optional[List] = None

    def _valueParts_hash(self):
        if not self.valueParts:
            return

        _hash = 0
        for part in self.valueParts:
            json_str = json.dumps(part, sort_keys=True)
            part_hash = zlib.crc32(json_str.encode())
            _hash ^= part_hash

        return _hash

    def __hash__(self):
        return hash((self.value, self._valueParts_hash()))

    def __eq__(self, other):
        return self.value == other.value and self._valueParts_hash() == other._valueParts_hash()


@dataclasses.dataclass(unsafe_hash=True)
class Location:
    spanId: int = dataclasses.field(compare=False, hash=False, repr=False)
    stackId: Optional[int] = dataclasses.field(init=False, compare=False)
    path: Optional[str] = None
    line: Optional[int] = None
    method: Optional[str] = dataclasses.field(compare=False, hash=False, repr=False, default="")
    class_name: Optional[str] = dataclasses.field(compare=False, hash=False, repr=False, default="")

    def __post_init__(self):
        self.hash = zlib.crc32(repr(self).encode())

    def __repr__(self):
        return f"Location(path='{self.path}', line={self.line})"

    def _to_dict(self):
        result = {}
        if self.spanId is not None:
            result["spanId"] = self.spanId
        if self.path:
            result["path"] = self.path
        if self.line is not None:
            result["line"] = self.line
        if self.method:
            result["method"] = self.method
        if self.class_name:
            result["class"] = self.class_name
        if self.stackId:
            result["stackId"] = str(self.stackId)
        return result


@dataclasses.dataclass(unsafe_hash=True)
class Vulnerability:
    type: str
    evidence: Evidence
    location: Location
    hash: int = dataclasses.field(init=False, compare=False, hash=("PYTEST_CURRENT_TEST" in os.environ), repr=False)

    def __post_init__(self):
        self.hash = zlib.crc32(repr(self).encode())

    def __repr__(self):
        return f"Vulnerability(type='{self.type}', location={self.location})"

    def _to_dict(self):
        to_dict = {
            "type": self.type,
            "evidence": self.evidence._to_dict(),
            "location": self.location._to_dict(),
            "hash": self.hash,
        }
        return to_dict


@dataclasses.dataclass
class Source(NotNoneDictable):
    origin: str
    name: str
    redacted: Optional[bool] = dataclasses.field(default=None, repr=False)
    value: Optional[str] = dataclasses.field(default=None, repr=False)
    pattern: Optional[str] = dataclasses.field(default=None, repr=False)

    def __hash__(self):
        """origin & name serve as hashes. This approach aims to mitigate false positives when searching for
        identical sources in a list, especially when sources undergo changes. The provided example illustrates how
        two sources with different attributes could actually represent the same source. For example:
        Source(origin=<OriginType.PARAMETER: 0>, name='string1', redacted=False, value="password", pattern=None)
        could be the same source as the one below:
        Source(origin=<OriginType.PARAMETER: 0>, name='string1', redacted=True, value=None, pattern='ab')
        :return:
        """
        return hash((self.origin, self.name))


@dataclasses.dataclass
class IastSpanReporter(NotNoneDictable):
    """
    Class representing an IAST span reporter.
    """

    stacktrace_id: int = 0
    sources: List[Source] = dataclasses.field(default_factory=list)
    vulnerabilities: Set[Vulnerability] = dataclasses.field(default_factory=set)

    def __hash__(self) -> int:
        """
        Computes the hash value of the IAST span reporter.

        Returns:
        - int: Hash value.
        """
        return reduce(operator.xor, (hash(obj) for obj in set(self.sources) | self.vulnerabilities))

    def __post_init__(self):
        """
        Populates the stacktrace_id of provided vulnerabilities if any.
        """
        for vulnerability in self.vulnerabilities:
            self._populate_stacktrace_id(vulnerability)

    def _append_vulnerability(self, vulnerability: Vulnerability) -> None:
        """
        Appends a vulnerability to the IAST span reporter.

        Args:
        - vulnerability (Vulnerability): Vulnerability to append.
        """
        self._populate_stacktrace_id(vulnerability)
        self.vulnerabilities.add(vulnerability)

    def _populate_stacktrace_id(self, vulnerability: Vulnerability) -> None:
        """
        Populates the stacktrace_id of the IAST span reporter.
        """
        self.stacktrace_id += 1

        str_id = str(self.stacktrace_id)
        if not report_stack(stack_id=str_id, namespace=STACK_TRACE.IAST):
            vulnerability.location.stackId = None
        else:
            vulnerability.location.stackId = self.stacktrace_id

    def _merge_json(self, json_str: str) -> None:
        """
        Merges the current IAST span reporter with another IAST span reporter from a JSON string.

        Args:
        - json_str (str): JSON string.
        """
        other = IastSpanReporter()
        other.stacktrace_id = self.stacktrace_id
        other._from_json(json_str)
        self._merge(other)
        self.stacktrace_id = other.stacktrace_id

    def _merge(self, other: "IastSpanReporter") -> None:
        """
        Merges the current IAST span reporter with another IAST span reporter.

        Args:
        - other (IastSpanReporter): IAST span reporter to merge.
        """
        len_previous_sources = len(self.sources)
        self.sources = self.sources + other.sources
        self._update_vulnerabilities(other, len_previous_sources)

    def _update_vulnerabilities(self, other: "IastSpanReporter", offset: int):
        for vuln in other.vulnerabilities:
            if (
                hasattr(vuln, "evidence")
                and hasattr(vuln.evidence, "valueParts")
                and vuln.evidence.valueParts is not None
            ):
                for part in vuln.evidence.valueParts:
                    if "source" in part:
                        part["source"] = part["source"] + offset
            self.vulnerabilities.add(vuln)

    def _from_json(self, json_str: str):
        """
        Initializes the IAST span reporter from a JSON string.

        Args:
        - json_str (str): JSON string.
        """
        from ._taint_tracking import str_to_origin

        data = json.loads(json_str)
        self.sources = []
        for i in data["sources"]:
            source = Source(
                origin=str_to_origin(i["origin"]),
                name=i["name"],
            )
            if "value" in i:
                source.value = i["value"]
            if "redacted" in i:
                source.redacted = i["redacted"]
            if "pattern" in i:
                source.pattern = i["pattern"]
            self.sources.append(source)

        self.vulnerabilities = set()
        for i in data["vulnerabilities"]:
            evidence = Evidence()
            if "ranges" in i["evidence"]:
                evidence._ranges = i["evidence"]["ranges"]
            if "value" in i["evidence"]:
                evidence.value = i["evidence"]["value"]
            if "valueParts" in i["evidence"]:
                evidence.valueParts = i["evidence"]["valueParts"]
            if "dialect" in i["evidence"]:
                evidence.dialect = i["evidence"]["dialect"]
            self._append_vulnerability(
                Vulnerability(
                    type=i["type"],
                    evidence=evidence,
                    location=Location(
                        spanId=i["location"]["spanId"],
                        path=i["location"]["path"],
                        line=i["location"]["line"],
                    ),
                )
            )

    def _to_dict(self):
        return {
            "sources": [i._to_dict() for i in self.sources],
            "vulnerabilities": [i._to_dict() for i in self.vulnerabilities],
        }

    @staticmethod
    def taint_ranges_as_evidence_info(pyobject: Any) -> Tuple[List[Source], List[Dict]]:
        """
        Extracts tainted ranges as evidence information.

        Args:
        - pyobject (Any): Python object.

        Returns:
        - Tuple[Set[Source], List[Dict]]: Set of Source objects and list of tainted ranges as dictionaries.
        """
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        sources = list()
        tainted_ranges = get_tainted_ranges(pyobject)
        tainted_ranges_to_dict = list()
        if not len(tainted_ranges):
            return [], []

        for _range in tainted_ranges:
            source = Source(origin=_range.source.origin, name=_range.source.name, value=_range.source.value)
            if source not in sources:
                sources.append(source)

            tainted_ranges_to_dict.append(
                {"start": _range.start, "end": _range.start + _range.length, "length": _range.length, "source": source}
            )
        return sources, tainted_ranges_to_dict

    def add_ranges_to_evidence_and_extract_sources(self, vuln):
        if not asm_config.is_iast_request_enabled:
            log.debug(
                "iast::propagation::context::add_ranges_to_evidence_and_extract_sources. "
                "No request quota or this vulnerability is outside the context"
            )
            return
        sources, tainted_ranges_to_dict = self.taint_ranges_as_evidence_info(vuln.evidence.value)
        vuln.evidence._ranges = tainted_ranges_to_dict
        for source in sources:
            if source not in self.sources:
                self.sources = self.sources + [source]

    def build_and_scrub_value_parts(self) -> Dict[str, Any]:
        """
        Builds and scrubs value parts of vulnerabilities.

        Returns:
        - Dict[str, Any]: Dictionary representation of the IAST span reporter.
        """
        if not asm_config.is_iast_request_enabled:
            log.debug(
                "iast::propagation::context::build_and_scrub_value_parts. "
                "No request quota or this vulnerability is outside the context"
            )
            return {}
        for vuln in self.vulnerabilities:
            scrubbing_result = sensitive_handler.scrub_evidence(
                vuln.type, vuln.evidence, vuln.evidence._ranges, self.sources
            )
            if scrubbing_result:
                redacted_value_parts = scrubbing_result["redacted_value_parts"]
                redacted_sources = scrubbing_result["redacted_sources"]
                i = 0
                for source in self.sources:
                    if i in redacted_sources:
                        source.value = None
                vuln.evidence.valueParts = redacted_value_parts
                vuln.evidence.value = None
            elif vuln.evidence.value is not None and vuln.type not in EVIDENCES_WITH_NO_SOURCES:
                vuln.evidence.valueParts = self.get_unredacted_value_parts(
                    vuln.evidence.value, vuln.evidence._ranges, self.sources
                )
                vuln.evidence.value = None
        return self._to_dict()

    def get_unredacted_value_parts(self, evidence_value: str, ranges: List[dict], sources: List[Any]) -> List[dict]:
        """
        Gets unredacted value parts of evidence.

        Args:
        - evidence_value (str): Evidence value.
        - ranges (List[Dict]): List of tainted ranges.
        - sources (List[Any]): List of sources.

        Returns:
        - List[Dict]: List of unredacted value parts.
        """
        value_parts = []
        from_index = 0

        for range_ in ranges:
            if from_index < range_["start"]:
                value_parts.append({"value": evidence_value[from_index : range_["start"]]})

            source_index = _get_source_index(sources, range_["source"])

            value_parts.append(
                {"value": evidence_value[range_["start"] : range_["end"]], "source": source_index}  # type: ignore[dict-item]
            )

            from_index = range_["end"]

        if from_index < len(evidence_value):
            value_parts.append({"value": evidence_value[from_index:]})

        return value_parts

    def _to_str(self, dict_data=None) -> str:
        """
        Converts the IAST span reporter to a JSON string.

        Returns:
        - str: JSON representation of the IAST span reporter.
        """
        from ._taint_tracking import OriginType
        from ._taint_tracking import origin_to_str

        class OriginTypeEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, OriginType):
                    # if the obj is uuid, we simply return the value of uuid
                    return origin_to_str(obj)
                return json.JSONEncoder.default(self, obj)

        if dict_data:
            return json.dumps(dict_data, cls=OriginTypeEncoder)
        return json.dumps(self._to_dict(), cls=OriginTypeEncoder)
