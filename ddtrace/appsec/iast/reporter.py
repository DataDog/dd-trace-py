from functools import reduce
import json
import operator
import os
from typing import List
from typing import Set
from typing import TYPE_CHECKING
import zlib

import attr

from ddtrace.internal.compat import PY2


if TYPE_CHECKING:
    import Any
    import Dict
    import Optional


def _only_if_true(value):
    return value if value else None


@attr.s(eq=False, hash=False)
class Evidence(object):
    value = attr.ib(type=str, default=None)  # type: Optional[str]
    pattern = attr.ib(type=str, default=None)  # type: Optional[str]
    valueParts = attr.ib(type=list, default=None)  # type: Optional[List[Dict[str, Any]]]
    redacted = attr.ib(type=bool, default=False, converter=_only_if_true)  # type: bool

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
        return hash((self.value, self.pattern, self._valueParts_hash(), self.redacted))

    def __eq__(self, other):
        return (
            self.value == other.value
            and self.pattern == other.pattern
            and self._valueParts_hash() == other._valueParts_hash()
            and self.redacted == other.redacted
        )


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
        if PY2 and self.hash < 0:
            self.hash += 1 << 32


@attr.s(eq=True, hash=True)
class Source(object):
    origin = attr.ib(type=str)  # type: str
    name = attr.ib(type=str)  # type: str
    redacted = attr.ib(type=bool, default=False, converter=_only_if_true)  # type: bool
    value = attr.ib(type=str, default=None)  # type: Optional[str]
    pattern = attr.ib(type=str, default=None)  # type: Optional[str]


@attr.s(eq=False, hash=False)
class IastSpanReporter(object):
    sources = attr.ib(type=List[Source], factory=list)  # type: List[Source]
    vulnerabilities = attr.ib(type=Set[Vulnerability], factory=set)  # type: Set[Vulnerability]

    def __hash__(self):
        return reduce(operator.xor, (hash(obj) for obj in set(self.sources) | self.vulnerabilities))
