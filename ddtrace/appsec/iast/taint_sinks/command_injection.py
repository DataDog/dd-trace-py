import re
from typing import List
from typing import TYPE_CHECKING
from typing import Union

import six

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._utils import _scrub_get_tokens_positions
from ddtrace.appsec.iast.constants import EVIDENCE_CMDI
from ddtrace.appsec.iast.constants import VULN_CMDI
from ddtrace.appsec.iast.taint_sinks._base import VulnerabilityBase


if TYPE_CHECKING:
    from typing import Any
    from typing import Dict

    from ddtrace.appsec.iast.reporter import Vulnerability

_INSIDE_QUOTES_REGEXP = re.compile(r"(?:\s|^)(\S*\/\S*)")


@oce.register
class CommandInjection(VulnerabilityBase):
    vulnerability_type = VULN_CMDI
    evidence_type = EVIDENCE_CMDI

    @classmethod
    def report(cls, evidence_value=None, sources=None):
        if isinstance(evidence_value, (str, bytes, bytearray)):
            from ddtrace.appsec.iast._taint_tracking import taint_ranges_as_evidence_info

            evidence_value, sources = taint_ranges_as_evidence_info(evidence_value)
        super(CommandInjection, cls).report(evidence_value=evidence_value, sources=sources)

    @classmethod
    def _extract_sensitive_tokens(cls, vulns_to_text):
        # type: (Dict[Vulnerability, str]) -> Dict[int, Dict[str, Any]]

        ret = {}  # type: Dict[int, Dict[str, Any]]
        for vuln, text in six.iteritems(vulns_to_text):
            vuln_hash = hash(vuln)
            ret[vuln_hash] = {
                "tokens": set(_INSIDE_QUOTES_REGEXP.findall(text)),
            }
            ret[vuln_hash]["token_positions"] = _scrub_get_tokens_positions(text, ret[vuln_hash]["tokens"])

        return ret


def _iast_report_cmdi(shell_args):
    # type: (Union[str, List[str]]) -> None
    report_cmdi = ""
    from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec.iast._taint_tracking.aspects import join_aspect

    if isinstance(shell_args, (list, tuple)):
        for arg in shell_args:
            if get_tainted_ranges(arg):
                report_cmdi = join_aspect(" ", shell_args)
                break
    elif get_tainted_ranges(shell_args):
        report_cmdi = shell_args

    if report_cmdi:
        CommandInjection.report(evidence_value=report_cmdi)
