import json
import os
from functools import reduce
import operator
import zlib

import attr

from ddtrace.internal.compat import PY2


class Evidence(object):
    def __init__(self, value=None, pattern=None, valueParts=None):
        self.value = value
        self.pattern = pattern
        self.valueParts = valueParts

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return (self.value == other.value and
                    self.pattern == other.pattern and
                    self._valueParts_hash() == other._valueParts_hash())
        return False

    def _valueParts_hash(self):
        if not self.valueParts:
            return

        hash = 0
        for part in self.valueParts:
            if isinstance(part, dict):
                json_str = json.dumps(part, sort_keys=True)
                part_hash = zlib.crc32(json_str.encode())
            else:
                part_hash = hash(part)
            hash ^= part_hash

        return hash

    def __hash__(self):
        return hash((self.value, self.pattern, self._valueParts_hash()))

    def __repr__(self):
        return f"Evidence(value={self.value}, pattern={self.pattern}, valueParts={self.valueParts}"

    def asdict(self, *args, **kwargs):
        ret = {}
        if self.value:
            ret["value"] = self.value
        if self.valueParts:
            ret["valueParts"] = self.valueParts
        return ret


@attr.s(eq=True, hash=True)
class Location(object):
    path = attr.ib(type=str)
    line = attr.ib(type=int)
    spanId = attr.ib(type=int, eq=False, hash=False, repr=False)


@attr.s(eq=True, hash=True)
class Vulnerability(object):
    type = attr.ib(type=str)
    evidence = attr.ib(type=Evidence, repr=True)
    location = attr.ib(type=Location, hash="PYTEST_CURRENT_TEST" in os.environ)
    hash = attr.ib(init=False, eq=False, hash=False, repr=False)

    def __attrs_post_init__(self):
        self.hash = zlib.crc32(repr(self).encode())
        if PY2 and self.hash < 0:
            self.hash += 1 << 32


@attr.s(eq=True, hash=True)
class Source(object):
    origin = attr.ib(type=str)
    name = attr.ib(type=str)
    value = attr.ib(type=str)
    redacted = attr.ib(type=bool, default=False)


class IastSpanReporter(object):
    def __init__(self, sources=None, vulnerabilities=None):
        self.sources = sources if sources else set()
        self.vulnerabilities = vulnerabilities if vulnerabilities else set()

    def __hash__(self):
        for obj in self.sources | self.vulnerabilities:
            print("Hash of %s -> %s" % (obj, hash(obj)))
        return reduce(operator.xor, (hash(obj) for obj in self.sources | self.vulnerabilities))

    def __str__(self):
        return str({"sources": self.sources, "vulnerabilities": self.vulnerabilities})

    def __repr__(self):
        return str(self)

    @staticmethod
    def _non_attrs_serializer(instance, field, value):
        if hasattr(value, "asdict"):
            return value.asdict()
        return value

    def asdict(self):

        JJJ = {
            "sources": [attr.asdict(s, filter=lambda attr, x: x is not None and x is not False) for s in self.sources],
            "vulnerabilities": [attr.asdict(s, filter=lambda attr, x: x is not None and x is not False, value_serializer=self._non_attrs_serializer) for s in self.vulnerabilities],
        }
        from pprint import pprint
        print("JJJ Report asdict: %s" % JJJ)
        pprint(JJJ)

        return {
            "sources": [attr.asdict(s, filter=lambda attr, x: x is not None and x is not False) for s in self.sources],
            "vulnerabilities": [attr.asdict(s, filter=lambda attr, x: x is not None and x is not False, value_serializer=self._non_attrs_serializer) for s in self.vulnerabilities],
        }
