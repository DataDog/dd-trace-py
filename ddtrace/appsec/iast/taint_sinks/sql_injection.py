import re
from typing import TYPE_CHECKING

import six

from .. import oce
from .._taint_tracking import taint_ranges_as_evidence_info
from .._utils import _scrub_get_tokens_positions
from ..constants import EVIDENCE_SQL_INJECTION
from ..constants import VULN_SQL_INJECTION
from ._base import VulnerabilityBase


if TYPE_CHECKING:
    from typing import Any
    from typing import Dict

    from .reporter import Vulnerability


_INSIDE_QUOTES_REGEXP = re.compile(r'["\']([^"\']*?)["\']')


@oce.register
class SqlInjection(VulnerabilityBase):
    vulnerability_type = VULN_SQL_INJECTION
    evidence_type = EVIDENCE_SQL_INJECTION

    @classmethod
    def report(cls, evidence_value=None, sources=None):
        if isinstance(evidence_value, (str, bytes, bytearray)):
            evidence_value, sources = taint_ranges_as_evidence_info(evidence_value)
        super(SqlInjection, cls).report(evidence_value=evidence_value, sources=sources)

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
