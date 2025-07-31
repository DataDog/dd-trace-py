import os
import sysconfig
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union

from ddtrace.appsec._deduplications import deduplication
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast.sampling.vulnerability_detection import rollback_quota
from ddtrace.appsec._iast.sampling.vulnerability_detection import should_process_vulnerability
from ddtrace.appsec._trace_utils import _asm_manual_keep
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ..._constants import IAST
from ..._constants import IAST_SPAN_TAGS
from .._iast_env import _get_iast_env
from .._iast_request_context import get_iast_reporter
from .._iast_request_context import set_iast_reporter
from .._span_metrics import increment_iast_span_metric
from .._stacktrace import get_info_frame
from ..reporter import Evidence
from ..reporter import IastSpanReporter
from ..reporter import Location
from ..reporter import Vulnerability


log = get_logger(__name__)

CWD = os.path.abspath(os.getcwd())

TEXT_TYPES = Union[str, bytes, bytearray]

PURELIB_PATH = sysconfig.get_path("purelib")
STDLIB_PATH = sysconfig.get_path("stdlib")


class taint_sink_deduplication(deduplication):
    def _check_deduplication(self):
        return asm_config._iast_deduplication_enabled

    def _extract(self, args):
        # We skip positions 0 and 1 because they represent the 'cls' and 'span' respectively
        return args[2:]


def _check_positions_contained(needle, container):
    needle_start, needle_end = needle
    container_start, container_end = container

    return (
        (container_start <= needle_start < container_end)
        or (container_start < needle_end <= container_end)
        or (needle_start <= container_start < needle_end)
        or (needle_start < container_end <= needle_end)
    )


class VulnerabilityBase:
    vulnerability_type = ""
    secure_mark = 0

    @staticmethod
    def has_quota():
        context = _get_iast_env()
        return context.vulnerability_budget < asm_config._iast_max_vulnerabilities_per_requests

    @classmethod
    @taint_sink_deduplication
    def _prepare_report(
        cls,
        vulnerability_type: str,
        evidence: Evidence,
        file_name: Optional[str],
        line_number: int,
        function_name: Optional[str] = None,
        class_name: Optional[str] = None,
        *args,
        **kwargs,
    ) -> bool:
        if line_number is not None and (line_number == 0 or line_number < -1):
            line_number = -1

        report = get_iast_reporter()
        span_id = 0
        span = core.get_root_span()
        if span:
            span_id = span.span_id
            # Mark the span as kept to avoid being dropped by the agent.
            #
            # It is important to do it as soon as the vulnerability is reported
            # to ensure that any downstream propagation performed has the new priority.
            _asm_manual_keep(span)

        vulnerability = Vulnerability(
            type=vulnerability_type,
            evidence=evidence,
            location=Location(
                path=file_name, line=line_number, spanId=span_id, method=function_name, class_name=class_name
            ),
        )
        if report:
            report._append_vulnerability(vulnerability)
        else:
            report = IastSpanReporter(vulnerabilities={vulnerability})
        report.add_ranges_to_evidence_and_extract_sources(vulnerability)

        set_iast_reporter(report)

        return True

    @classmethod
    def _compute_file_line(cls) -> Tuple[Optional[str], Optional[int], Optional[str], Optional[str]]:
        file_name = line_number = function_name = class_name = None
        frame_info = get_info_frame()
        if not frame_info or frame_info[0] in ("", -1):
            return file_name, line_number, function_name, class_name

        file_name, line_number, function_name, class_name = frame_info
        if not file_name:
            return None, None, None, None

        file_name = cls._rel_path(file_name)
        if not file_name:
            log.debug("Could not relativize vulnerability location path: %s", frame_info[0])
            return None, None, None, None

        return file_name, line_number, function_name, class_name

    @staticmethod
    def _rel_path(file_name: str) -> str:
        if file_name.startswith(PURELIB_PATH):
            return os.path.relpath(file_name, start=PURELIB_PATH)
        if file_name.startswith(STDLIB_PATH):
            return os.path.relpath(file_name, start=STDLIB_PATH)
        if file_name.startswith(CWD):
            return os.path.relpath(file_name, start=CWD)
        return ""

    @classmethod
    def _create_evidence_and_report(
        cls,
        vulnerability_type: str,
        evidence_value: TEXT_TYPES = "",
        dialect: Optional[str] = None,
        file_name: Optional[str] = None,
        line_number: Optional[int] = None,
        function_name: Optional[str] = None,
        class_name: Optional[str] = None,
        *args,
        **kwargs,
    ):
        if isinstance(evidence_value, IAST.TEXT_TYPES):
            if isinstance(evidence_value, (bytes, bytearray)):
                evidence_value = evidence_value.decode("utf-8")
            evidence = Evidence(value=evidence_value, dialect=dialect)
        else:
            log.debug("Unexpected evidence_value type: %s", type(evidence_value))
            evidence = Evidence(value="", dialect=dialect)
        return cls._prepare_report(
            vulnerability_type, evidence, file_name, line_number, function_name, class_name, *args, **kwargs
        )

    @classmethod
    def report(cls, evidence_value: TEXT_TYPES = "", dialect: Optional[str] = None) -> bool:
        """Build a IastSpanReporter instance to report it in the `AppSecIastSpanProcessor` as a string JSON"""
        result = False
        if should_process_vulnerability(cls.vulnerability_type):
            file_name = line_number = function_name = class_name = None

            if not getattr(cls, "skip_location", False):
                file_name, line_number, function_name, class_name = cls._compute_file_line()
                if file_name is None:
                    rollback_quota(cls.vulnerability_type)
                    return result
            # Evidence is a string in weak cipher, weak hash and weak randomness
            result = cls._create_evidence_and_report(
                cls.vulnerability_type, evidence_value, dialect, file_name, line_number, function_name, class_name
            )
            # If result is None that's mean deduplication raises and no vulnerability wasn't reported, with that,
            # we need to restore the quota
            if not result:
                rollback_quota(cls.vulnerability_type)
        return result

    @classmethod
    def is_tainted_pyobject(cls, string_to_check: TEXT_TYPES, origins_to_exclude: Set[OriginType] = set()) -> bool:
        """Check if a string contains tainted ranges that are not marked as secure and don't come exclusively
        from excluded origins.

        A string is considered tainted when:
        1. It has one or more taint ranges (indicating user-controlled data)
        2. At least one of these ranges is NOT marked as secure with the current vulnerability type's secure mark
           (cls.secure_mark)
        3. If origins_to_exclude is provided, at least one range must have an origin NOT in the excluded list
           for the string to be considered tainted

        The origins_to_exclude parameter allows filtering out specific taint sources:
        - If any range has an origin different from those in origins_to_exclude, the string is considered tainted
        - If ALL ranges come from excluded origins, the string is NOT considered tainted
        - If origins_to_exclude is empty, only conditions 1 and 2 are checked

        Example:
        - origins_to_exclude=[COOKIE]
        - String has ranges from: [COOKIE, PARAMETER]
        - Result: Tainted (because PARAMETER origin is not excluded)

        - origins_to_exclude=[COOKIE]
        - String has ranges from: [COOKIE, COOKIE]
        - Result: Not tainted (because all ranges are from excluded origins)

        Args:
            string_to_check (Text): The string to check for taint ranges and secure marks
            origins_to_exclude (list): List of origins to exclude from taint consideration

        Returns:
            bool: True if any range lacks the secure mark AND has a non-excluded origin, False otherwise
        """
        if len(ranges := get_ranges(string_to_check)) == 0:
            return False
        if not all(_range.has_secure_mark(cls.secure_mark) for _range in ranges):
            if origins_to_exclude:
                return not all(_range.source.origin in origins_to_exclude for _range in ranges)
            else:
                return True
        else:
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_SUPPRESSED_VULNERABILITY, cls.vulnerability_type)
        return False
