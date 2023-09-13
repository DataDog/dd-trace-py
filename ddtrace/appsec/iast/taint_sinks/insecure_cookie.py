from typing import TYPE_CHECKING

from ddtrace.internal.compat import six

from .. import oce
from ..constants import EVIDENCE_COOKIE
from ..constants import VULN_INSECURE_COOKIE
from ..constants import VULN_NO_HTTPONLY_COOKIE
from ..constants import VULN_NO_SAMESITE_COOKIE
from ..taint_sinks._base import VulnerabilityBase


if TYPE_CHECKING:
    from typing import Dict
    from typing import Optional


@oce.register
class InsecureCookie(VulnerabilityBase):
    vulnerability_type = VULN_INSECURE_COOKIE
    evidence_type = EVIDENCE_COOKIE
    scrub_evidence = False
    skip_location = True


@oce.register
class NoHttpOnlyCookie(VulnerabilityBase):
    vulnerability_type = VULN_NO_HTTPONLY_COOKIE
    evidence_type = EVIDENCE_COOKIE
    skip_location = True


@oce.register
class NoSameSite(VulnerabilityBase):
    vulnerability_type = VULN_NO_SAMESITE_COOKIE
    evidence_type = EVIDENCE_COOKIE
    skip_location = True


def asm_check_cookies(cookies):  # type: (Optional[Dict[str, str]]) -> None
    if not cookies:
        return

    for cookie_key, cookie_value in six.iteritems(cookies):
        lvalue = cookie_value.lower().replace(" ", "")
        evidence = "%s=%s" % (cookie_key, cookie_value)

        if ";secure" not in lvalue:
            InsecureCookie.report(evidence_value=evidence)
            return

        if ";httponly" not in lvalue:
            NoHttpOnlyCookie.report(evidence_value=evidence)
            return

        if ";samesite=" in lvalue:
            ss_tokens = lvalue.split(";samesite=")
            if len(ss_tokens) == 0:
                report_samesite = True
            elif ss_tokens[1].startswith("strict") or ss_tokens[1].startswith("lax"):
                report_samesite = False
            else:
                report_samesite = True
        else:
            report_samesite = True

        if report_samesite:
            NoSameSite.report(evidence_value=evidence)
