from typing import Set
import zlib

import attr


@attr.s(eq=True, hash=True)
class Evidence(object):
    value = attr.ib(type=str, default=None)
    valueParts = attr.ib(type=Set, default=None, hash=False)


@attr.s(eq=True, hash=True)
class Location(object):
    path = attr.ib(type=str)
    line = attr.ib(type=int)
    spanId = attr.ib(type=int, eq=False, hash=False)


@attr.s(eq=True, hash=True)
class Vulnerability(object):
    type = attr.ib(type=str)
    evidence = attr.ib(type=Evidence)
    location = attr.ib(type=Location)
    hash = attr.ib(init=False, eq=False, hash=False)

    def __attrs_post_init__(self):
        self.hash = zlib.crc32(":".join({self.location.path, str(self.location.line), self.type}))
        # DEV: Uncomment when we add support for Python 2
        # if six.PY2:
        #     self.hash += (1 << 32)


@attr.s(eq=True, hash=True)
class Source(object):
    origin = attr.ib(type=str)
    name = attr.ib(type=str)
    value = attr.ib(type=str)


@attr.s(eq=False)
class IastSpanReporter(object):
    sources = attr.ib(type=Set[Source], factory=set)
    vulnerabilities = attr.ib(type=Set[Vulnerability], factory=set)
