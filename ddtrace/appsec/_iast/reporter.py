import dataclasses
from functools import reduce
import json
import operator
import os
from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import List
from typing import Set
from typing import Tuple
import zlib

from ddtrace.appsec._iast._evidence_redaction import sensitive_handler
from ddtrace.appsec._iast._utils import _get_source_index
from ddtrace.appsec._iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec._iast.constants import VULN_WEAK_CIPHER_TYPE
from ddtrace.appsec._iast.constants import VULN_WEAK_RANDOMNESS


if TYPE_CHECKING:  # pragma: no cover
    from typing import Optional  # noqa:F401


ATTRS_TO_SKIP = frozenset({"_ranges", "_evidences_with_no_sources", "dialect"})


class Evidence:
    def __init__(self, dialect: Optional[str] = None, value: Optional[str] = None):
        self.dialect: Optional[str] = dialect
        self.value: Optional[str] = value
        self._ranges: List[Dict] = []
        self.valueParts: Optional[List] = None

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


class Location:
    def __init__(self, spanId: int, path: Optional[str] = None, line: Optional[int] = None):
        self.spanId = spanId
        self.path = path
        self.line = line

    def __hash__(self):
        return hash((self.spanId, self.path, self.line))

    def __eq__(self, other):
        return self.spanId == other.spanId and self.path == other.path and self.line == other.line


@dataclasses.dataclass
class Vulnerability:
    type: str
    evidence: Evidence
    location: Location
    hash: int = dataclasses.field(init=False, compare=False, hash=("PYTEST_CURRENT_TEST" in os.environ), repr=False)

    def __post_init__(self):
        self.hash = zlib.crc32(repr(self).encode())

    def __repr__(self):
        return f"Vulnerability(type={self.type}, location={self.location})"


@dataclasses.dataclass
class Source:
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


class IastSpanReporter:
    def __init__(self):
        self.sources: List[Source] = []
        self.vulnerabilities: Set[Vulnerability] = set()
        self._evidences_with_no_sources = [VULN_INSECURE_HASHING_TYPE, VULN_WEAK_CIPHER_TYPE, VULN_WEAK_RANDOMNESS]

    def __hash__(self) -> int:
        """
        Computes the hash value of the IAST span reporter.

        Returns:
        - int: Hash value.
        """
        return reduce(operator.xor, (hash(obj) for obj in set(self.sources) | self.vulnerabilities))

    @staticmethod
    def taint_ranges_as_evidence_info(pyobject: Any) -> Tuple[List[Source], List[Dict]]:
        """
        Extracts tainted ranges as evidence information.

        Args:
        - pyobject (Any): Python object.

        Returns:
        - Tuple[Set[Source], List[Dict]]: Set of Source objects and list of tainted ranges as dictionaries.
        """
        from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges

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
            elif vuln.evidence.value is not None and vuln.type not in self._evidences_with_no_sources:
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

    def _to_dict(self) -> Dict[str, Any]:
        """
        Converts the IAST span reporter to a dictionary.

        Returns:
        - Dict[str, Any]: Dictionary representation of the IAST span reporter.
        """
        return {k: v for k, v in dataclasses.asdict(self) if k not in ATTRS_TO_SKIP}  # type: ignore

    def _to_str(self) -> str:
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

        return json.dumps(self._to_dict(), cls=OriginTypeEncoder)
