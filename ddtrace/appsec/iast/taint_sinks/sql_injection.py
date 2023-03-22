from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast.constants import EVIDENCE_SQL_INJECTION
from ddtrace.appsec.iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec.iast.taint_sinks._base import VulnerabilityBase


@oce.register
class SqlInjection(VulnerabilityBase):
    vulnerability_type = VULN_SQL_INJECTION
    evidence_type = EVIDENCE_SQL_INJECTION
