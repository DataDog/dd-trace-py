import re
from typing import TYPE_CHECKING  # noqa:F401

from .. import oce
from .._taint_tracking import taint_ranges_as_evidence_info
from .._utils import _has_to_scrub
from .._utils import _is_numeric
from .._utils import _scrub_get_tokens_positions
from ..constants import EVIDENCE_SQL_INJECTION
from ..constants import VULN_SQL_INJECTION
from ._base import VulnerabilityBase


if TYPE_CHECKING:
    from typing import Any  # noqa:F401
    from typing import Dict  # noqa:F401

    from .reporter import Vulnerability  # noqa:F401

from sqlparse import parse
from sqlparse import tokens


_TEXT_TOKENS_REGEXP = re.compile(r"\b\w+\b")


@oce.register
class SqlInjection(VulnerabilityBase):
    vulnerability_type = VULN_SQL_INJECTION
    evidence_type = EVIDENCE_SQL_INJECTION
    redact_report = True

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

        in_singleline_comment = False

        for part in vuln.evidence.valueParts:
            source = part.get("source")
            value = part.get("value")

            if not value or part.get("redacted"):
                new_valueparts.append(part)
                continue

            parsed = parse(value)[0].flatten()
            out = []

            for item in parsed:
                if item.ttype == tokens.Whitespace.Newline:
                    in_singleline_comment = False

                elif in_singleline_comment:
                    # Skip all tokens after a -- comment until newline
                    continue

                if item.ttype in {
                    tokens.Literal.String.Single,
                    tokens.Literal.String.Double,
                    tokens.Literal.String.Symbol,
                    tokens.Literal.Number.Integer,
                    tokens.Literal.Number.Float,
                    tokens.Literal.Number.Hexadecimal,
                    tokens.Comment.Single,
                    tokens.Comment.Multiline,
                    tokens.Name,
                }:
                    redact_fully = False
                    add_later = None
                    sitem = str(item)

                    if _is_numeric(sitem):
                        redact_fully = True
                    elif item.ttype == tokens.Literal.String.Single or (
                        item.ttype == tokens.Literal.String.Symbol and "'" in str(item)
                    ):
                        out.append("'")
                        add_later = "'"
                        str_item = sitem.replace("'", "")
                        if _is_numeric(str_item):
                            redact_fully = True
                    elif item.ttype == tokens.Literal.String.Double or (
                        item.ttype == tokens.Literal.String.Symbol and '"' in str(item)
                    ):
                        out.append('"')
                        add_later = '"'
                        str_item = sitem.replace('"', "")
                        if _is_numeric(str_item):
                            redact_fully = True
                    elif item.ttype == tokens.Comment.Single:
                        out.append("--")
                        add_later = ""
                        redact_fully = True
                        in_singleline_comment = True
                    elif item.ttype == tokens.Comment.Multiline:
                        out.append("/*")
                        add_later = "*/"
                        redact_fully = True
                    elif item.ttype in (tokens.Number.Integer, tokens.Number.Float, tokens.Number.Hexadecimal):
                        redact_fully = True
                    else:
                        out.append(sitem)
                        continue

                    if len(out):
                        new_valueparts.append(_maybe_with_source(source, "".join(out)))

                    if redact_fully:
                        # Comments are totally redacted
                        new_valueparts.append({"redacted": True})
                    else:
                        new_valueparts.append(_maybe_with_source(source, str_item))

                    if add_later:
                        out = [add_later]
                    else:
                        out = []
                else:
                    out.append(str(item))

            if len(out):
                new_valueparts.append(_maybe_with_source(source, "".join(out)))

        # Scrub as needed
        idx = 0
        len_parts = len(new_valueparts)
        while idx < len_parts:
            value = new_valueparts[idx].get("value")
            if value and _has_to_scrub(value) and idx < (len_parts - 1) and "redacted" not in new_valueparts[idx + 1]:
                # Scrub the value, which is the next one, except when the previous was a LIKE  or an assignment
                # in which case this is the value to scrub
                prev_valuepart = new_valueparts[idx - 1].get("value", "").strip().lower()
                if len(prev_valuepart) and (" like " in prev_valuepart or prev_valuepart[-1] == "="):
                    new_valueparts[idx] = {"redacted": True}
                else:  # scrub the next non empty quote value
                    for part in new_valueparts[idx + 1 :]:
                        idx += 1
                        next_valuepart = part.get("value", "").strip()
                        if not len(next_valuepart) or next_valuepart in ("'", '"'):
                            continue

                        new_valueparts[idx] = {"redacted": True}
                        break
            idx += 1

        vuln.evidence.valueParts = new_valueparts
