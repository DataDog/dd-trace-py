from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast.constants import EVIDENCE_CMDI
from ddtrace.appsec.iast.constants import VULN_CMDI
from ddtrace.appsec.iast.taint_sinks._base import VulnerabilityBase


@oce.register
class CommandInjection(VulnerabilityBase):
    vulnerability_type = VULN_CMDI
    evidence_type = EVIDENCE_CMDI
