from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges


def add_secure_mark(string_to_secure: object, vulnerability_types: list[VulnerabilityType]) -> None:
    if isinstance(string_to_secure, (str, bytes)):
        ranges = get_tainted_ranges(string_to_secure)
        if ranges:
            for _range in ranges:
                for vuln_type in vulnerability_types:
                    _range.add_secure_mark(vuln_type)
