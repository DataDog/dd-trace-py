import re
from typing import TYPE_CHECKING  # noqa:F401

from .. import oce
from .._taint_tracking import taint_ranges_as_evidence_info
from .._utils import _scrub_get_tokens_positions
from ..constants import EVIDENCE_SQL_INJECTION
from ..constants import VULN_SQL_INJECTION
from ._base import VulnerabilityBase


if TYPE_CHECKING:
    from typing import Any  # noqa:F401
    from typing import Dict  # noqa:F401

    from .reporter import Vulnerability  # noqa:F401


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
        for vuln, text in vulns_to_text.items():
            vuln_hash = hash(vuln)
            ret[vuln_hash] = {
                "tokens": set(_INSIDE_QUOTES_REGEXP.findall(text)),
            }
            ret[vuln_hash]["token_positions"] = _scrub_get_tokens_positions(text, ret[vuln_hash]["tokens"])

        return ret
