from typing import Set

import attr


@attr.s(eq=True, hash=True)
class Evidence(object):
    type = attr.ib(type=str)
    value = attr.ib(type=str, default="")


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
        self.hash = hash(self.type) ^ hash(self.evidence) ^ hash(self.location)


@attr.s(eq=True, hash=True)
class Source(object):
    origin = attr.ib(type=str)
    name = attr.ib(type=str)
    value = attr.ib(type=str)


@attr.s(eq=False)
class IastSpanReporter(object):
    sources = attr.ib(type=Set[Source], factory=set)
    vulnerabilities = attr.ib(type=Set[Vulnerability], factory=set)
