import os
import re
import subprocess  # nosec
from typing import TYPE_CHECKING  # noqa:F401
from typing import List  # noqa:F401
from typing import Set  # noqa:F401
from typing import Union  # noqa:F401

from ddtrace.contrib import trace_utils
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import increment_iast_span_metric
from .._utils import _has_to_scrub
from .._utils import _scrub
from .._utils import _scrub_get_tokens_positions
from ..constants import EVIDENCE_CMDI
from ..constants import VULN_CMDI
from ._base import VulnerabilityBase
from ._base import _check_positions_contained


if TYPE_CHECKING:
    from typing import Any  # noqa:F401
    from typing import Dict  # noqa:F401

    from ..reporter import IastSpanReporter  # noqa:F401
    from ..reporter import Vulnerability  # noqa:F401


log = get_logger(__name__)

_INSIDE_QUOTES_REGEXP = re.compile(r"^(?:\s*(?:sudo|doas)\s+)?\b\S+\b\s*(.*)")


def get_version():
    # type: () -> str
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not getattr(os, "_datadog_cmdi_patch", False):
        trace_utils.wrap(os, "system", _iast_cmdi_ossystem)

        # all os.spawn* variants eventually use this one:
        trace_utils.wrap(os, "_spawnvef", _iast_cmdi_osspawn)

    if not getattr(subprocess, "_datadog_cmdi_patch", False):
        trace_utils.wrap(subprocess, "Popen.__init__", _iast_cmdi_subprocess_init)

        os._datadog_cmdi_patch = True
        subprocess._datadog_cmdi_patch = True


def unpatch():
    # type: () -> None
    trace_utils.unwrap(os, "system")
    trace_utils.unwrap(os, "_spawnvef")
    trace_utils.unwrap(subprocess.Popen, "__init__")

    os._datadog_cmdi_patch = False  # type: ignore[attr-defined]
    subprocess._datadog_cmdi_patch = False  # type: ignore[attr-defined]


def _iast_cmdi_ossystem(wrapped, instance, args, kwargs):
    _iast_report_cmdi(args[0])
    return wrapped(*args, **kwargs)


def _iast_cmdi_osspawn(wrapped, instance, args, kwargs):
    mode, file, func_args, _, _ = args
    _iast_report_cmdi(func_args)

    return wrapped(*args, **kwargs)


def _iast_cmdi_subprocess_init(wrapped, instance, args, kwargs):
    cmd_args = args[0] if len(args) else kwargs["args"]
    _iast_report_cmdi(cmd_args)

    return wrapped(*args, **kwargs)


@oce.register
class CommandInjection(VulnerabilityBase):
    vulnerability_type = VULN_CMDI
    evidence_type = EVIDENCE_CMDI

    @classmethod
    def report(cls, evidence_value=None, sources=None):
        if isinstance(evidence_value, (str, bytes, bytearray)):
            from .._taint_tracking import taint_ranges_as_evidence_info

            evidence_value, sources = taint_ranges_as_evidence_info(evidence_value)
        super(CommandInjection, cls).report(evidence_value=evidence_value, sources=sources)

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

    @classmethod
    def replace_tokens(
        cls,
        vuln,
        vulns_to_tokens,
        has_range=False,
    ):
        ret = vuln.evidence.value
        replaced = False

        for token in vulns_to_tokens[hash(vuln)]["tokens"]:
            ret = ret.replace(token, "")
            replaced = True

        return ret, replaced

    @classmethod
    def _redact_report(cls, report):  # type: (IastSpanReporter) -> IastSpanReporter
        if not asm_config._iast_redaction_enabled:
            return report

        # See if there is a match on either any of the sources or value parts of the report
        found = False

        for source in report.sources:
            # Join them so we only run the regexps once for each source
            joined_fields = "%s%s" % (source.name, source.value)
            if _has_to_scrub(joined_fields):
                found = True
                break

        vulns_to_text = {}

        if not found:
            # Check the evidence's value/s
            for vuln in report.vulnerabilities:
                vulnerability_text = cls._get_vulnerability_text(vuln)
                if _has_to_scrub(vulnerability_text) or _INSIDE_QUOTES_REGEXP.match(vulnerability_text):
                    vulns_to_text[vuln] = vulnerability_text
                    found = True
                    break

        if not found:
            return report

        if not vulns_to_text:
            vulns_to_text = {vuln: cls._get_vulnerability_text(vuln) for vuln in report.vulnerabilities}

        # If we're here, some potentially sensitive information was found, we delegate on
        # the specific subclass the task of extracting the variable tokens (e.g. literals inside
        # quotes for SQL Injection). Note that by just having one potentially sensitive match
        # we need to then scrub all the tokens, thus why we do it in two steps instead of one
        vulns_to_tokens = cls._extract_sensitive_tokens(vulns_to_text)

        if not vulns_to_tokens:
            return report

        all_tokens = set()  # type: Set[str]
        for _, value_dict in vulns_to_tokens.items():
            all_tokens.update(value_dict["tokens"])

        # Iterate over all the sources, if one of the tokens match it, redact it
        for source in report.sources:
            if source.name in "".join(all_tokens) or source.value in "".join(all_tokens):
                source.pattern = _scrub(source.value, has_range=True)
                source.redacted = True
                source.value = None

        # Same for all the evidence values
        try:
            for vuln in report.vulnerabilities:
                # Use the initial hash directly as iteration key since the vuln itself will change
                vuln_hash = hash(vuln)
                if vuln.evidence.value is not None:
                    pattern, replaced = cls.replace_tokens(
                        vuln, vulns_to_tokens, hasattr(vuln.evidence.value, "source")
                    )
                    if replaced:
                        vuln.evidence.pattern = pattern
                        vuln.evidence.redacted = True
                        vuln.evidence.value = None
                elif vuln.evidence.valueParts is not None:
                    idx = 0
                    new_value_parts = []
                    for part in vuln.evidence.valueParts:
                        value = part["value"]
                        part_len = len(value)
                        part_start = idx
                        part_end = idx + part_len
                        pattern_list = []

                        for positions in vulns_to_tokens[vuln_hash]["token_positions"]:
                            if _check_positions_contained(positions, (part_start, part_end)):
                                part_scrub_start = max(positions[0] - idx, 0)
                                part_scrub_end = positions[1] - idx
                                pattern_list.append(value[:part_scrub_start] + "" + value[part_scrub_end:])
                                if part.get("source", False) is not False:
                                    source = report.sources[part["source"]]
                                    if source.redacted:
                                        part["redacted"] = source.redacted
                                        part["pattern"] = source.pattern
                                        del part["value"]
                                    new_value_parts.append(part)
                                    break
                                else:
                                    part["value"] = "".join(pattern_list)
                                    new_value_parts.append(part)
                                    new_value_parts.append({"redacted": True})
                                    break
                            else:
                                new_value_parts.append(part)
                                pattern_list.append(value[part_start:part_end])
                                break

                        idx += part_len
                    vuln.evidence.valueParts = new_value_parts
        except (ValueError, KeyError):
            log.debug("an error occurred while redacting cmdi", exc_info=True)
        return report


def _iast_report_cmdi(shell_args):
    # type: (Union[str, List[str]]) -> None
    report_cmdi = ""
    from .._metrics import _set_metric_iast_executed_sink
    from .._taint_tracking import is_pyobject_tainted
    from .._taint_tracking.aspects import join_aspect

    if isinstance(shell_args, (list, tuple)):
        for arg in shell_args:
            if is_pyobject_tainted(arg):
                report_cmdi = join_aspect(" ".join, 1, " ", shell_args)
                break
    elif is_pyobject_tainted(shell_args):
        report_cmdi = shell_args

    if report_cmdi:
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, CommandInjection.vulnerability_type)
        _set_metric_iast_executed_sink(CommandInjection.vulnerability_type)
        CommandInjection.report(evidence_value=report_cmdi)
