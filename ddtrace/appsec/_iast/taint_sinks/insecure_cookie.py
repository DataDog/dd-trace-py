from typing import TYPE_CHECKING  # noqa:F401

from ddtrace.internal.compat import six

from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import _set_metric_iast_executed_sink
from .._metrics import increment_iast_span_metric
from ..constants import EVIDENCE_COOKIE
from ..constants import VULN_INSECURE_COOKIE
from ..constants import VULN_NO_HTTPONLY_COOKIE
from ..constants import VULN_NO_SAMESITE_COOKIE
from ..taint_sinks._base import VulnerabilityBase


if TYPE_CHECKING:
    from typing import Dict  # noqa:F401
    from typing import Optional  # noqa:F401


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

        if ";secure" not in lvalue:
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, InsecureCookie.vulnerability_type)
            _set_metric_iast_executed_sink(InsecureCookie.vulnerability_type)
            InsecureCookie.report(evidence_value=cookie_key)

        if ";httponly" not in lvalue:
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, NoHttpOnlyCookie.vulnerability_type)
            _set_metric_iast_executed_sink(NoHttpOnlyCookie.vulnerability_type)
            NoHttpOnlyCookie.report(evidence_value=cookie_key)

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
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, NoSameSite.vulnerability_type)
            _set_metric_iast_executed_sink(NoSameSite.vulnerability_type)
            NoSameSite.report(evidence_value=cookie_key)
