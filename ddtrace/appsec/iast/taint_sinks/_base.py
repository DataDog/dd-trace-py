import os
from typing import TYPE_CHECKING

import six

from ddtrace import tracer
from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec.iast._overhead_control_engine import Operation
from ddtrace.appsec.iast._util import _has_to_scrub
from ddtrace.appsec.iast._util import _is_evidence_value_parts
from ddtrace.appsec.iast._util import _scrub
from ddtrace.appsec.iast.reporter import Evidence
from ddtrace.appsec.iast.reporter import IastSpanReporter
from ddtrace.appsec.iast.reporter import Location
from ddtrace.appsec.iast.reporter import Source
from ddtrace.appsec.iast.reporter import Vulnerability
from ddtrace.internal import _context
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import LFUCache
from ddtrace.settings import _config


try:
    # Python >= 3.4
    from ddtrace.appsec.iast._stacktrace import get_info_frame
except ImportError:
    # Python 2
    from ddtrace.appsec.iast._stacktrace_py2 import get_info_frame

if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import List
    from typing import Optional
    from typing import Set
    from typing import Text

    from ddtrace.appsec.iast._input_info import Input_info

log = get_logger(__name__)

CWD = os.path.abspath(os.getcwd())


class VulnerabilityBase(Operation):
    vulnerability_type = ""
    evidence_type = ""
    _redacted_report_cache = LFUCache()

    @classmethod
    def wrap(cls, func):
        # type: (Callable) -> Callable
        def wrapper(wrapped, instance, args, kwargs):
            # type: (Callable, Any, Any, Any) -> Any
            """Get the current root Span and attach it to the wrapped function. We need the span to report the vulnerability
            and update the context with the report information.
            """
            if oce.request_has_quota and cls.has_quota():
                return func(wrapped, instance, args, kwargs)
            else:
                log.debug("IAST: no vulnerability quota to analyze more sink points")
            return wrapped(*args, **kwargs)

        return wrapper

    @classmethod
    def report(cls, evidence_value="", sources=None):
        # type: (Text, Optional[List[Input_info]]) -> None
        """Build a IastSpanReporter instance to report it in the `AppSecIastSpanProcessor` as a string JSON

        TODO: check deduplications if DD_IAST_DEDUPLICATION_ENABLED is true
        """

        if cls.acquire_quota():
            if not tracer or not hasattr(tracer, "current_root_span"):
                log.debug("Not tracer or tracer has no root span")
                return None

            span = tracer.current_root_span()
            if not span:
                log.debug("No root span in the current execution. Skipping IAST taint sink.")
                return None

            frame_info = get_info_frame(CWD)
            if not frame_info:
                return None

            file_name, line_number = frame_info

            # Remove CWD prefix
            if file_name.startswith(CWD):
                file_name = os.path.relpath(file_name, start=CWD)

            if _is_evidence_value_parts(evidence_value):
                evidence = Evidence(valueParts=evidence_value)
            elif isinstance(evidence_value, (str, bytes, bytearray)):
                evidence = Evidence(value=evidence_value)
            else:
                log.debug("Unexpected evidence_value type: %s", type(evidence_value))
                evidence = ""

            if not cls.is_not_reported(file_name, line_number):
                # not not reported = reported
                return None

            _set_metric_iast_executed_sink(cls.vulnerability_type)

            report = _context.get_item(IAST.CONTEXT_KEY, span=span)
            if report:
                report.vulnerabilities.add(
                    Vulnerability(
                        type=cls.vulnerability_type,
                        evidence=evidence,
                        location=Location(path=file_name, line=line_number, spanId=span.span_id),
                    )
                )

            else:
                report = IastSpanReporter(
                    vulnerabilities={
                        Vulnerability(
                            type=cls.vulnerability_type,
                            evidence=evidence,
                            location=Location(path=file_name, line=line_number, spanId=span.span_id),
                        )
                    }
                )
            if sources:
                report.sources = {Source(origin=x.origin, name=x.name, value=x.value) for x in sources}

            redacted_report = cls._redacted_report_cache.get(hash(report), lambda: cls._redact_report(report), False)
            _context.set_item(IAST.CONTEXT_KEY, redacted_report, span=span)

    @classmethod
    def _extract_sensitive_tokens(cls, report):
        # type: (IastSpanReporter) -> Set[str]
        log.debug("Base class VulnerabilityBase._extract_sensitive_tokens called")

    @classmethod
    def _get_vulnerability_text(cls, vulnerability):
        if vulnerability and vulnerability.evidence.value is not None:
            return vulnerability.evidence.value

        if vulnerability.evidence.valueParts is not None:
            return "".join([part.get("value", "") for part in vulnerability.evidence.valueParts])

        return ""

    @classmethod
    def _redact_report(cls, report):  # type: (IastSpanReporter) -> IastSpanReporter
        if not _config._iast_redaction_enabled:
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
                if _has_to_scrub(vulnerability_text):
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

        all_tokens = set()
        for _, value_dict in six.iteritems(vulns_to_tokens):
            all_tokens.update(value_dict["tokens"])

        # Iterate over all the sources, if one of the tokens match it, redact it
        for source in report.sources:
            if source.name in all_tokens or source.value in all_tokens:
                source.pattern = _scrub(source.value, has_range=True)
                source.redacted = True
                source.value = None

        def replace_tokens(vuln, has_range=False):
            ret = vuln.evidence.value
            replaced = False

            for token in vulns_to_tokens[hash(vuln)]["tokens"]:
                ret = ret.replace(token, _scrub(token, has_range))
                replaced = True

            return ret, replaced

        # Same for all the evidence values
        for vuln in report.vulnerabilities:
            # Use the initial hash directly as iteration key since the vuln itself will change
            vuln_hash = hash(vuln)
            if vuln.evidence.value is not None:
                pattern, replaced = replace_tokens(vuln, hasattr(vuln.evidence.value, "source"))
                if replaced:
                    vuln.evidence.pattern = pattern
                    vuln.evidence.redacted = True
                    vuln.evidence.value = None
            elif vuln.evidence.valueParts is not None:
                idx = 0
                for part in vuln.evidence.valueParts:
                    value = part["value"]
                    part_len = len(value)
                    part_start = idx
                    part_end = idx + part_len

                    for positions in vulns_to_tokens[vuln_hash]["token_positions"]:
                        if part_end <= positions[0]:
                            # This part if before this token
                            continue
                        elif (part_start <= positions[0] < part_end) or (part_end > positions[1]):
                            # This part contains at least part of the token
                            part_scrub_start = max(positions[0] - idx, 0)
                            part_scrub_end = positions[1] - idx
                            to_scrub = value[part_scrub_start:part_scrub_end]
                            scrubbed = _scrub(to_scrub, "source" in part)
                            part["pattern"] = value[:part_scrub_start] + scrubbed + value[part_scrub_end:]
                            part["redacted"] = True
                            del part["value"]
                    idx += part_len

        return report
