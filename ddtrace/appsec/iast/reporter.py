from typing import Set

import attr


@attr.s(eq=False)
class Evidence(object):
    type = attr.ib(type=str)
    value = attr.ib(type=str, default="")

    def __eq__(self, other):
        return self.type == other.type and self.value == other.value

    def __hash__(self):
        return hash(self.type) ^ hash(self.value)


@attr.s(eq=False)
class Location(object):
    path = attr.ib(type=str)
    line = attr.ib(type=int)

    def __eq__(self, other):
        return self.path == other.path and self.line == other.line

    def __hash__(self):
        return hash(self.path) ^ hash(self.line)


@attr.s(eq=False)
class Vulnerability(object):
    type = attr.ib(type=str)
    evidence = attr.ib(type=Evidence)
    location = attr.ib(type=Location)
    span_id = attr.ib(type=int)

    @property
    def hash(self):
        return hash(self)

    def __eq__(self, other):
        return self.type == other.type and self.evidence == other.evidence and self.location == other.location

    def __hash__(self):
        return hash(self.type) ^ hash(self.evidence) ^ hash(self.location)


@attr.s(eq=False)
class Source(object):
    origin = attr.ib(type=str)
    name = attr.ib(type=str)
    value = attr.ib(type=str)

    def __eq__(self, other):
        return self.origin == other.origin and self.name == other.name and self.value == other.value

    def __hash__(self):
        return hash(self.origin) ^ hash(self.name) ^ hash(self.value)


@attr.s(eq=False)
class IastSpanReporter(object):
    sources = attr.ib(type=Set[Source], factory=set)
    vulnerabilities = attr.ib(type=Set[Vulnerability], factory=set)
