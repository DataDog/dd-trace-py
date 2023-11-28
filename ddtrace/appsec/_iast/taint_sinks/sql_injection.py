import re
from typing import TYPE_CHECKING

import six

from .. import oce
from .._taint_tracking import taint_ranges_as_evidence_info
from .._utils import _scrub_get_tokens_positions, _has_to_scrub
from ..constants import EVIDENCE_SQL_INJECTION
from ..constants import VULN_SQL_INJECTION
from ._base import VulnerabilityBase


if TYPE_CHECKING:
    from typing import Any
    from typing import Dict

    from .reporter import Vulnerability


_TEXT_TOKENS_REGEXP = re.compile(r'\b\w+\b')
_INSIDE_QUOTES_REGEXP = re.compile(r'[\"\']([^"\']*?)[\"\']')
_INSIDE_QUOTES_REGEXP = re.compile(r'''(['"])(.*?)\1''')


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
                "tokens": set(_TEXT_TOKENS_REGEXP.findall(text)),
            }
            ret[vuln_hash]["token_positions"] = _scrub_get_tokens_positions(text, ret[vuln_hash]["tokens"])

        return ret

    @classmethod
    def _custom_edit_valueparts(cls, vuln):
        def _maybe_with_source(source, value):
            if source is not None:
                return {"value": value, "source": source}
            return {"value": value}
        new_valueparts = []
        print("JJJ original valueParts:\n%s" % vuln.evidence.valueParts)
        for part in vuln.evidence.valueParts:
            value = part.get("value")

            if not value or part.get("redacted"):
                new_valueparts.append(part)
                continue

            print("JJJ part: %s" % part)
            prev = 0
            source = part.get("source")
            for token in _INSIDE_QUOTES_REGEXP.finditer(value):
                # JJJ copy source if exists!
                new_valueparts.append(_maybe_with_source(source, value[prev:token.start(2)]))
                new_valueparts.append(_maybe_with_source(source, value[token.start(2):token.end(2)]))
                prev = token.end(2)
            new_valueparts.append(_maybe_with_source(source, value[prev:]))

        # Scrub as needed
        idx = 0
        len_parts = len(new_valueparts)
        while idx < len_parts:
            value = new_valueparts[idx].get("value")
            if value and _has_to_scrub(value) and idx < (len_parts - 1):
                # Scrub the value, which is the next one
                # JJJ check source and ranges?
                new_valueparts[idx+1] = {"redacted": True}
                idx += 2
                continue
            idx += 1

        vuln.evidence.valueParts = new_valueparts
        print("JJJ new valueParts:\n%s" % new_valueparts)

