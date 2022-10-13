from typing import Set
from typing import TYPE_CHECKING

import attr

from ddtrace.appsec.iast.stacktrace import get_info_frame
from ddtrace.constants import IAST_CONTEXT_KEY
from ddtrace.internal import _context


if TYPE_CHECKING:  # pragma: no cover

    from typing import Text

    from ddtrace.span import Span


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


def report_vulnerability(span, vulnerability_type, evidence_type, evidence_value=""):
    # type: (Span, Text, Text, Text) -> None
    """Build a IastSpanReporter instance to report it in the `AppSecIastSpanProcessor` as a string JSON"""
    report = _context.get_item(IAST_CONTEXT_KEY, span=span)
    file_name, line_number = get_info_frame()
    if report:
        report.vulnerabilities.add(
            Vulnerability(
                type=vulnerability_type,
                evidence=Evidence(type=evidence_type, value=evidence_value),
                location=Location(path=file_name, line=line_number),
            )
        )

    else:
        report = IastSpanReporter(
            vulnerabilities={
                Vulnerability(
                    type=vulnerability_type,
                    evidence=Evidence(type=evidence_type, value=evidence_value),
                    location=Location(path=file_name, line=line_number),
                )
            }
        )

    _context.set_item(IAST_CONTEXT_KEY, report, span=span)
