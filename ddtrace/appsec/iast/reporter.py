from typing import Set

import attr


@attr.s(eq=False)
class Evidence(object):
    type = attr.ib(type=str)
    value = attr.ib(type=str, default="")


@attr.s(eq=False)
class Location(object):
    path = attr.ib(type=str)
    line = attr.ib(type=int)


@attr.s(eq=False)
class Vulnerability(object):
    type = attr.ib(type=str)
    evidence = attr.ib(type=Evidence)
    location = attr.ib(type=Location)


@attr.s(eq=False)
class Source(object):
    origin = attr.ib(type=str)
    name = attr.ib(type=str)
    value = attr.ib(type=str)


@attr.s(eq=False)
class IastSpanReporter(object):
    sources = attr.ib(type=Set[Source], factory=set)
    vulnerabilities = attr.ib(type=Set[Vulnerability], factory=set)
