import os
from typing import TYPE_CHECKING
from typing import cast

from ddtrace import tracer
from ddtrace.appsec._constants import IAST
from ddtrace.internal import core
from ddtrace.internal.compat import six
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import LFUCache
from ddtrace.settings import _config

from .. import oce
from .._metrics import _set_metric_iast_executed_sink
from .._overhead_control_engine import Operation
from .._utils import _has_to_scrub
from .._utils import _is_evidence_value_parts
from .._utils import _scrub
from ..reporter import Evidence
from ..reporter import IastSpanReporter
from ..reporter import Location
from ..reporter import Source
from ..reporter import Vulnerability


try:
    # Python >= 3.4
    from .._stacktrace import get_info_frame
except ImportError:
    # Python 2
    from .._stacktrace_py2 import get_info_frame

if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Set
    from typing import Text
    from typing import Union

log = get_logger(__name__)

CWD = os.path.abspath(os.getcwd())


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
    def _reset_cache(cls):
        cls._redacted_report_cache.clear()

    @classmethod
    def wrap(cls, func):
        # type: (Callable) -> Callable
        def wrapper(wrapped, instance, args, kwargs):
            # type: (Callable, Any, Any, Any) -> Any
            """Get the current root Span and attach it to the wrapped function. We need the span to report the
            vulnerability and update the context with the report information.
            """
            if oce.request_has_quota and cls.has_quota():
                return func(wrapped, instance, args, kwargs)
            else:
                log.debug("IAST: no vulnerability quota to analyze more sink points")
            return wrapped(*args, **kwargs)

        return wrapper

    @classmethod
    def report(cls, evidence_value="", sources=None):
        # type: (Union[Text|List[Dict[str, Any]]], Optional[List[Source]]) -> None
        """Build a IastSpanReporter instance to report it in the `AppSecIastSpanProcessor` as a string JSON

        TODO: check deduplications if DD_IAST_DEDUPLICATION_ENABLED is true
        """

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
                if not frame_info:
                    return None

                file_name, line_number = frame_info

                # Remove CWD prefix
                if file_name.startswith(CWD):
                    file_name = os.path.relpath(file_name, start=CWD)

                if not cls.is_not_reported(file_name, line_number):
                    return

            if _is_evidence_value_parts(evidence_value):
                evidence = Evidence(valueParts=evidence_value)
            # Evidence is a string in weak cipher, weak hash and weak randomness
            elif isinstance(evidence_value, (str, bytes, bytearray)):
                evidence = Evidence(value=evidence_value)
            else:
                log.debug("Unexpected evidence_value type: %s", type(evidence_value))
                evidence = Evidence(value="")

            _set_metric_iast_executed_sink(cls.vulnerability_type)

            report = core.get_item(IAST.CONTEXT_KEY, span=span)
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

                def cast_value(value):
                    if isinstance(value, (bytes, bytearray)):
                        value_decoded = value.decode("utf-8")
                    else:
                        value_decoded = value
                    return value_decoded

                report.sources = [Source(origin=x.origin, name=x.name, value=cast_value(x.value)) for x in sources]

            redacted_report = cls._redacted_report_cache.get(
                hash(report), lambda x: cls._redact_report(cast(IastSpanReporter, report))
            )
            core.set_item(IAST.CONTEXT_KEY, redacted_report, span=span)

    @classmethod
    def _extract_sensitive_tokens(cls, report):
        # type: (Dict[Vulnerability, str]) -> Dict[int, Dict[str, Any]]
        log.debug("Base class VulnerabilityBase._extract_sensitive_tokens called")
        return {}

    @classmethod
    def _get_vulnerability_text(cls, vulnerability):
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
        ret = vuln.evidence.value
        replaced = False

        for token in vulns_to_tokens[hash(vuln)]["tokens"]:
            ret = ret.replace(token, _scrub(token, has_range))
            replaced = True

        return ret, replaced

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

        all_tokens = set()  # type: Set[str]
        for _, value_dict in six.iteritems(vulns_to_tokens):
            all_tokens.update(value_dict["tokens"])

        # Iterate over all the sources, if one of the tokens match it, redact it
        for source in report.sources:
            if source.name in all_tokens or source.value in all_tokens:
                source.pattern = _scrub(source.value, has_range=True)
                source.redacted = True
                source.value = None

        # Same for all the evidence values
        for vuln in report.vulnerabilities:
            # Use the initial hash directly as iteration key since the vuln itself will change
            vuln_hash = hash(vuln)
            if vuln.evidence.value is not None:
                pattern, replaced = cls.replace_tokens(vuln, vulns_to_tokens, hasattr(vuln.evidence.value, "source"))
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
                    pattern_list = []

                    for positions in vulns_to_tokens[vuln_hash]["token_positions"]:
                        if _check_positions_contained(positions, (part_start, part_end)):
                            part_scrub_start = max(positions[0] - idx, 0)
                            part_scrub_end = positions[1] - idx
                            to_scrub = value[part_scrub_start:part_scrub_end]
                            scrubbed = _scrub(to_scrub, "source" in part)
                            pattern_list.append(value[:part_scrub_start] + scrubbed + value[part_scrub_end:])
                            part["redacted"] = True
                        else:
                            pattern_list.append(value[part_start:part_end])
                            continue

                    if "redacted" in part:
                        part["pattern"] = "".join(pattern_list)
                        del part["value"]

                    idx += part_len

        return report
