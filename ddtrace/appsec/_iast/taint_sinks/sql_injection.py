from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase


@oce.register
class SqlInjection(VulnerabilityBase):
    vulnerability_type = VULN_SQL_INJECTION
