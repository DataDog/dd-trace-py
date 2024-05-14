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

import attr

from ddtrace.appsec._iast._evidence_redaction import sensitive_handler
from ddtrace.appsec._iast._utils import _get_source_index
from ddtrace.appsec._iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec._iast.constants import VULN_WEAK_CIPHER_TYPE
from ddtrace.appsec._iast.constants import VULN_WEAK_RANDOMNESS


if TYPE_CHECKING:  # pragma: no cover
    from typing import Optional  # noqa:F401


def _only_if_true(value):
    return value if value else None


ATTRS_TO_SKIP = frozenset({"_ranges", "_evidences_with_no_sources", "dialect"})


@attr.s(eq=False, hash=False)
class Evidence(object):
    dialect = attr.ib(type=str, default=None)  # type: Optional[str]
    value = attr.ib(type=str, default=None)  # type: Optional[str]
    _ranges = attr.ib(type=dict, default={})  # type: Any
    valueParts = attr.ib(type=list, default=None)  # type: Any

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


@attr.s(eq=True, hash=True)
class Location(object):
    spanId = attr.ib(type=int, eq=False, hash=False, repr=False)  # type: int
    path = attr.ib(type=str, default=None)  # type: Optional[str]
    line = attr.ib(type=int, default=None)  # type: Optional[int]


@attr.s(eq=True, hash=True)
class Vulnerability(object):
    type = attr.ib(type=str)  # type: str
    evidence = attr.ib(type=Evidence, repr=False)  # type: Evidence
    location = attr.ib(type=Location, hash="PYTEST_CURRENT_TEST" in os.environ)  # type: Location
    hash = attr.ib(init=False, eq=False, hash=False, repr=False)  # type: int

    def __attrs_post_init__(self):
        self.hash = zlib.crc32(repr(self).encode())


@attr.s(eq=True, hash=False)
class Source(object):
    origin = attr.ib(type=str)  # type: str
    name = attr.ib(type=str)  # type: str
    redacted = attr.ib(type=bool, default=False, converter=_only_if_true)  # type: bool
    value = attr.ib(type=str, default=None)  # type: Optional[str]
    pattern = attr.ib(type=str, default=None)  # type: Optional[str]

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


@attr.s(eq=False, hash=False)
class IastSpanReporter(object):
    """
    Class representing an IAST span reporter.
    """

    sources = attr.ib(type=List[Source], factory=list)  # type: List[Source]
    vulnerabilities = attr.ib(type=Set[Vulnerability], factory=set)  # type: Set[Vulnerability]
    _evidences_with_no_sources = [VULN_INSECURE_HASHING_TYPE, VULN_WEAK_CIPHER_TYPE, VULN_WEAK_RANDOMNESS]

    def __hash__(self) -> int:
        """
        Computes the hash value of the IAST span reporter.

        Returns:
        - int: Hash value.
        """
        return reduce(operator.xor, (hash(obj) for obj in set(self.sources) | self.vulnerabilities))

    def taint_ranges_as_evidence_info(self, pyobject: Any) -> Tuple[List[Source], List[Dict]]:
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
        return attr.asdict(
            self,
            filter=lambda attr, x: x is not None and attr.name not in ATTRS_TO_SKIP,
        )

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
