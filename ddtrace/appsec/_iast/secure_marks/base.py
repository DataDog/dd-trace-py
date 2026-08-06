from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges


def add_secure_mark(value: object, vulnerability_types: list[VulnerabilityType]) -> None:
    for _range in get_tainted_ranges(value):
        for vuln_type in vulnerability_types:
            _range.add_secure_mark(vuln_type)
