import os
from typing import TYPE_CHECKING

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

            redacted_report = cls._redacted_report_cache.get(hash(report), lambda: cls.redact(report), False)
            _context.set_item(IAST.CONTEXT_KEY, redacted_report, span=span)

    @classmethod
    def _extract_sensitive_tokens(cls, report):
        # type: (IastSpanReporter) -> Set[str]
        raise Exception("JJJ this is abstract, but do this right")

    @classmethod
    def redact_report(cls, report):  # type: (IastSpanReporter) -> IastSpanReporter
        # TODO: add some catching. Most request will have the same fields

        if not _config._iast_redaction_enabled:
            return report

        # See if there is a match on either any of the sources or value parts of the report
        found = False

        for source in report.sources:
            # Join them so we only run the regexps once for source
            joined_fields = "%s%s" % (source.name, source.value)
            if _has_to_scrub(joined_fields):
                found = True
                break

        if not found:
            # Check the evidence's value/s
            for vulnerability in report.vulnerabilities:
                if vulnerability.evidence.value is not None and _has_to_scrub(vulnerability.evidence.value):
                    found = True
                    break
                elif vulnerability.evidence.valueParts is not None:
                    # Join all the strings in valueParts so we run only the regexp once per vulnerability
                    joined_parts = "".join([part.get("value", "") for part in vulnerability.evidence.valueParts])
                    if _has_to_scrub(joined_parts):
                        found = True
                        break

        if not found:
            return report

        # If we're here, some potentially sensitive information was found, we delegate on
        # the specific subclass the task of extracting the variable tokens (e.g. literals inside
        # quotes for SQL Injection). Note that by just having one potentially sensitive match
        # we need to then scrub all the tokens, thus why we do it in two steps instead of one
        tokens = cls._extract_sensitive_tokens(report)

        if not tokens:
            return report

        redacted_tokens = {t: _scrub(t) for t in tokens}

        # Iterate over all the sources, if one of the tokens match it, redact it
        for source in report.sources:
            if source.name in tokens or source.value in tokens:
                source.value = redacted_tokens(source.value)
                source.redacted = True

        def replace_tokens(s):
            ret = s
            for token in tokens:
                ret = ret.replace(token, redacted_tokens(token))
            return s

        # Same for all the evidence values
        for vulnerability in report.vulnerabilities:
            if vulnerability.evidence.value is not None:
                orig_vuln = vulnerability.evidence.value
                vulnerability.evidence.value = replace_tokens(vulnerability.evidence.value)
                vulnerability.evidence.redacted = orig_vuln != vulnerability.evidence.value
            elif vulnerability.evidence.valueParts is not None:
                for part in vulnerability.evidence.valueParts:
                    value = part.get("value", "")
                    if not value:
                        continue

                    part["value"] = replace_tokens(value)
                    if value != part["value"]:
                        part["redacted"] = True

        return report
