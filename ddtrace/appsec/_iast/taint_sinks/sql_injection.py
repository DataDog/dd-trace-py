import re
from typing import TYPE_CHECKING

import six

from .. import oce
from .._taint_tracking import taint_ranges_as_evidence_info
from .._utils import _scrub_get_tokens_positions, _has_to_scrub, _is_numeric
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

        from sqlparse import parse, tokens

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
                print("JJJ XXX in_singleline_comment: %s" % in_singleline_comment)
                if item.ttype == tokens.Whitespace.Newline:
                    print("JJJ XXX end of singleline comment")
                    in_singleline_comment = False

                elif in_singleline_comment:
                    # Skip all tokens after a -- comment until newline
                    print("JJJ XXX skipping because prev was SingleLine Comment")
                    continue

                else:
                    print("JJJ XXX other case, in_singleline_comment: %s" % in_singleline_comment)

                print("JJJ parsed item: %s" % str(item))
                print("JJJ parsed item type: %s" % str(item.ttype))
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
                        print("JJJ XXX is numeric: %s" % sitem)
                    elif item.ttype == tokens.Literal.String.Single or (item.ttype == tokens.Literal.String.Symbol and "'" in str(item)):
                        out.append("'")
                        add_later = "'"
                        str_item = sitem.replace("'", "")
                        if _is_numeric(str_item):
                            redact_fully = True
                    elif item.ttype == tokens.Literal.String.Double or (item.ttype == tokens.Literal.String.Symbol and '"' in str(item)):
                        out.append('"')
                        add_later = '"'
                        str_item = sitem.replace('"', "")
                        if _is_numeric(str_item):
                            redact_fully = True
                    elif item.ttype == tokens.Comment.Single:
                        out.append("--")
                        add_later = ""
                        redact_fully = True
                        print("JJJ XXX setting in_singleline_comment to True")
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
                        new_valueparts.append(_maybe_with_source(source, ''.join(out)))

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
                    print("JJJ adding to out: %s" % str(item))
                    out.append(str(item))

            if len(out):
                new_valueparts.append(_maybe_with_source(source, ''.join(out)))
            print("JJJ new_valueparts2: %s" % new_valueparts)

            print("JJJ part: %s" % part)

        print("JJJ new valueparts: %s" % new_valueparts)

        # Scrub as needed
        idx = 0
        len_parts = len(new_valueparts)
        while idx < len_parts:
            value = new_valueparts[idx].get("value")
            if value and _has_to_scrub(value) and idx < (len_parts - 1) and "redacted" not in new_valueparts[idx+1]:
                # Scrub the value, which is the next one
                # JJJ check source and ranges
                new_valueparts[idx+1] = {"redacted": True}
                idx += 2
                continue
            idx += 1

        vuln.evidence.valueParts = new_valueparts
        print("JJJ new valueParts:\n%s" % new_valueparts)

