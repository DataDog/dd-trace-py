import os
from typing import TYPE_CHECKING  # noqa:F401
from typing import cast  # noqa:F401

from ddtrace import tracer
from ddtrace.appsec._constants import IAST
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import LFUCache
from ddtrace.settings.asm import config as asm_config

from ..._deduplications import deduplication
from .._overhead_control_engine import Operation
from .._stacktrace import get_info_frame
from .._utils import _has_to_scrub
from .._utils import _is_evidence_value_parts
from .._utils import _scrub
from ..processor import AppSecIastSpanProcessor
from ..reporter import Evidence
from ..reporter import IastSpanReporter
from ..reporter import Location
from ..reporter import Vulnerability


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any  # noqa:F401
    from typing import Callable  # noqa:F401
    from typing import Dict  # noqa:F401
    from typing import List  # noqa:F401
    from typing import Optional  # noqa:F401
    from typing import Set  # noqa:F401
    from typing import Text  # noqa:F401
    from typing import Union  # noqa:F401

log = get_logger(__name__)

CWD = os.path.abspath(os.getcwd())


class taint_sink_deduplication(deduplication):
    def _extract(self, args):
        # we skip 0, 1 and last position because its the cls, span and sources respectively
        return args[2:-1]


def _check_positions_contained(needle, container):
    needle_start, needle_end = needle
    container_start, container_end = container

    return (
        (container_start <= needle_start < container_end)
        or (container_start < needle_end <= container_end)
        or (needle_start <= container_start < needle_end)
        or (needle_start < container_end <= needle_end)
    )


class VulnerabilityBase(Operation):
    vulnerability_type = ""
    evidence_type = ""
    _redacted_report_cache = LFUCache()

    @classmethod
    def _reset_cache_for_testing(cls):
        """Reset the redacted reports and deduplication cache. For testing purposes only."""
        cls._redacted_report_cache.clear()

    @classmethod
    def wrap(cls, func):
        # type: (Callable) -> Callable
        def wrapper(wrapped, instance, args, kwargs):
            # type: (Callable, Any, Any, Any) -> Any
            """Get the current root Span and attach it to the wrapped function. We need the span to report the
            vulnerability and update the context with the report information.
            """
            if AppSecIastSpanProcessor.is_span_analyzed() and cls.has_quota():
                return func(wrapped, instance, args, kwargs)
            else:
                log.debug("IAST: no vulnerability quota to analyze more sink points")
            return wrapped(*args, **kwargs)

        return wrapper

    @classmethod
    @taint_sink_deduplication
    def _prepare_report(cls, span, vulnerability_type, evidence, file_name, line_number, sources):
        if line_number is not None and (line_number == 0 or line_number < -1):
            line_number = -1

        report = core.get_item(IAST.CONTEXT_KEY, span=span)
        vulnerability = Vulnerability(
            type=vulnerability_type,
            evidence=evidence,
            location=Location(path=file_name, line=line_number, spanId=span.span_id),
        )
        if report:
            report.vulnerabilities.add(vulnerability)
        else:
            report = IastSpanReporter(vulnerabilities={vulnerability})
        report.add_ranges_to_evidence_and_extract_sources(vulnerability)

        if getattr(cls, "redact_report", False):
            redacted_report = cls._redacted_report_cache.get(
                hash(report), lambda x: cls._redact_report(cast(IastSpanReporter, report))
            )
        else:
            redacted_report = report
        core.set_item(IAST.CONTEXT_KEY, redacted_report, span=span)

        return True

    @classmethod
    def report(cls, evidence_value="", value_parts=None, sources=None):
        # type: (Any, Any, Optional[List[Any]]) -> None
        """Build a IastSpanReporter instance to report it in the `AppSecIastSpanProcessor` as a string JSON"""
        # TODO: type of evidence_value will be Text. We wait to finish the redaction refactor.
        if cls.acquire_quota():
            if not tracer or not hasattr(tracer, "current_root_span"):
                log.debug(
                    "[IAST] VulnerabilityReporter is trying to report an evidence, "
                    "but not tracer or tracer has no root span"
                )
                return None

            span = tracer.current_root_span()
            if not span:
                log.debug(
                    "[IAST] VulnerabilityReporter. No root span in the current execution. Skipping IAST taint sink."
                )
                return None

            file_name = None
            line_number = None

            skip_location = getattr(cls, "skip_location", False)
            if not skip_location:
                frame_info = get_info_frame(CWD)
                if not frame_info or frame_info[0] == "" or frame_info[0] == -1:
                    return None

                file_name, line_number = frame_info

                # Remove CWD prefix
                if file_name.startswith(CWD):
                    file_name = os.path.relpath(file_name, start=CWD)

                if not cls.is_not_reported(file_name, line_number):
                    return

            # TODO: This function is deprecated, but we need to migrate all vulnerabilities first before deleting it
            if _is_evidence_value_parts(evidence_value) or _is_evidence_value_parts(value_parts):
                evidence = Evidence(value=evidence_value, valueParts=value_parts)
            # Evidence is a string in weak cipher, weak hash and weak randomness
            elif isinstance(evidence_value, (str, bytes, bytearray)):
                evidence = Evidence(value=evidence_value)  # type: ignore
            else:
                log.debug("Unexpected evidence_value type: %s", type(evidence_value))
                evidence = Evidence(value="")

            result = cls._prepare_report(span, cls.vulnerability_type, evidence, file_name, line_number, sources)
            # If result is None that's mean deduplication raises and no vulnerability wasn't reported, with that,
            # we need to restore the quota
            if not result:
                cls.increment_quota()

    @classmethod
    def _extract_sensitive_tokens(cls, report):
        # type: (Dict[Vulnerability, str]) -> Dict[int, Dict[str, Any]]
        # TODO: This function is deprecated.
        #  Redaction migrated to `ddtrace.appsec._iast._evidence_redaction._sensitive_handler` but we need to migrate
        #  all vulnerabilities to use it first.
        log.debug("Base class VulnerabilityBase._extract_sensitive_tokens called")
        return {}

    @classmethod
    def _get_vulnerability_text(cls, vulnerability):
        # TODO: This function is deprecated.
        #  Redaction migrated to `ddtrace.appsec._iast._evidence_redaction._sensitive_handler` but we need to migrate
        #  all vulnerabilities to use it first.
        if vulnerability and vulnerability.evidence.value is not None:
            return vulnerability.evidence.value

        if vulnerability.evidence.valueParts is not None:
            return "".join(
                [
                    (part.get("value", "") if type(part) is not str else part)
                    for part in vulnerability.evidence.valueParts
                ]
            )

        return ""

    @classmethod
    def replace_tokens(
        cls,
        vuln,
        vulns_to_tokens,
        has_range=False,
    ):
        # TODO: This function is deprecated.
        #  Redaction migrated to `ddtrace.appsec._iast._evidence_redaction._sensitive_handler` but we need to migrate
        #  all vulnerabilities to use it first.
        ret = vuln.evidence.value
        replaced = False

        for token in vulns_to_tokens[hash(vuln)]["tokens"]:
            ret = ret.replace(token, _scrub(token, has_range))
            replaced = True

        return ret, replaced

    @classmethod
    def _custom_edit_valueparts(cls, vuln):
        # Subclasses could optionally implement this to add further processing to the
        # vulnerability valueParts
        # TODO: This function is deprecated.
        #  Redaction migrated to `ddtrace.appsec._iast._evidence_redaction._sensitive_handler` but we need to migrate
        #  all vulnerabilities to use it first.
        return

    @classmethod
    def _redact_report(cls, report):  # type: (IastSpanReporter) -> IastSpanReporter
        # TODO: This function is deprecated.
        #  Redaction migrated to `ddtrace.appsec._iast._evidence_redaction._sensitive_handler` but we need to migrate
        #  all vulnerabilities to use it first.
        if not asm_config._iast_redaction_enabled:
            return report

        # See if there is a match on either any of the sources or value parts of the report
        already_scrubbed = {}

        sources_values_to_scrubbed = {}
        vulns_to_text = {vuln: cls._get_vulnerability_text(vuln) for vuln in report.vulnerabilities}
        vulns_to_tokens = cls._extract_sensitive_tokens(vulns_to_text)

        for source in report.sources:
            # Join them so we only run the regexps once for each source
            # joined_fields = "%s%s" % (source.name, source.value)
            if _has_to_scrub(source.name) or _has_to_scrub(source.value):  # type: ignore
                scrubbed = _scrub(source.value, has_range=True)  # type: ignore
                already_scrubbed[source.value] = scrubbed
                source.redacted = True
                sources_values_to_scrubbed[source.value] = scrubbed
                source.pattern = scrubbed
                source.value = None

        already_scrubbed_set = set(already_scrubbed.keys())
        for vuln in report.vulnerabilities:
            if vuln.evidence.value is not None:
                pattern, replaced = cls.replace_tokens(vuln, vulns_to_tokens, hasattr(vuln.evidence.value, "source"))
                if replaced:
                    vuln.evidence.value = None

            if vuln.evidence.valueParts is None:
                continue
            for part in vuln.evidence.valueParts:
                part_value = part.get("value")
                if not part_value:
                    continue

                if part_value in already_scrubbed_set:
                    part["pattern"] = already_scrubbed[part["value"]]
                    part["redacted"] = True
                    del part["value"]

            cls._custom_edit_valueparts(vuln)
        return report
