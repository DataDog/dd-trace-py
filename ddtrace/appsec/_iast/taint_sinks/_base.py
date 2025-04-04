import os
from typing import Any
from typing import Callable
from typing import Optional
from typing import Text
from typing import Tuple

from ddtrace.appsec._deduplications import deduplication
from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._iast_request_context import set_iast_reporter
from ddtrace.appsec._iast._overhead_control_engine import Operation
from ddtrace.appsec._iast._stacktrace import get_info_frame
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._utils import _is_iast_debug_enabled
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._trace_utils import _asm_manual_keep
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import tracer


log = get_logger(__name__)

CWD = os.path.abspath(os.getcwd())


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


class VulnerabilityBase(Operation):
    vulnerability_type = ""
    secure_mark = 0

    @classmethod
    def wrap(cls, func: Callable) -> Callable:
        def wrapper(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
            """Get the current root Span and attach it to the wrapped function. We need the span to report the
            vulnerability and update the context with the report information.
            """
            if not asm_config.is_iast_request_enabled:
                if _is_iast_debug_enabled():
                    log.debug(
                        "iast::propagation::context::VulnerabilityBase.wrapper. No request quota or this vulnerability "
                        "is outside the context"
                    )
                return wrapped(*args, **kwargs)
            elif cls.has_quota():
                return func(wrapped, instance, args, kwargs)
            else:
                return wrapped(*args, **kwargs)

        return wrapper

    @classmethod
    @taint_sink_deduplication
    def _prepare_report(
        cls,
        vulnerability_type,
        evidence,
        file_name,
        line_number,
        function_name: Text = "",
        class_name: Text = "",
        *args,
        **kwargs,
    ) -> bool:
        if not asm_config.is_iast_request_enabled:
            if _is_iast_debug_enabled():
                log.debug(
                    "iast::propagation::context::VulnerabilityBase._prepare_report. "
                    "No request quota or this vulnerability is outside the context"
                )
            return False
        if line_number is not None and (line_number == 0 or line_number < -1):
            line_number = -1

        report = get_iast_reporter()
        span_id = 0
        if tracer and hasattr(tracer, "current_root_span"):
            span = tracer.current_root_span()
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
            report.vulnerabilities.add(vulnerability)
        else:
            report = IastSpanReporter(vulnerabilities={vulnerability})
        report.add_ranges_to_evidence_and_extract_sources(vulnerability)

        set_iast_reporter(report)

        return True

    @classmethod
    def _compute_file_line(cls) -> Tuple[Optional[Text], Optional[int], Optional[Text], Optional[Text]]:
        file_name = line_number = function_name = class_name = None

        frame_info = get_info_frame(CWD)
        if not frame_info or frame_info[0] in ("", -1):
            return file_name, line_number, function_name, class_name

        file_name, line_number, function_name, class_name = frame_info

        if file_name.startswith(CWD):
            file_name = os.path.relpath(file_name, start=CWD)

        if not cls.is_not_reported(file_name, line_number):
            return None, None, None, None

        return file_name, line_number, function_name, class_name

    @classmethod
    def _create_evidence_and_report(
        cls,
        vulnerability_type: Text,
        evidence_value: Text = "",
        dialect: Optional[Text] = None,
        file_name: Optional[Text] = None,
        line_number: Optional[int] = None,
        function_name: Optional[Text] = None,
        class_name: Optional[Text] = None,
        *args,
        **kwargs,
    ):
        if isinstance(evidence_value, (str, bytes, bytearray)):
            evidence = Evidence(value=evidence_value, dialect=dialect)
        else:
            log.debug("Unexpected evidence_value type: %s", type(evidence_value))
            evidence = Evidence(value="", dialect=dialect)
        return cls._prepare_report(
            vulnerability_type, evidence, file_name, line_number, function_name, class_name, *args, **kwargs
        )

    @classmethod
    def report(cls, evidence_value: Text = "", dialect: Optional[Text] = None) -> None:
        """Build a IastSpanReporter instance to report it in the `AppSecIastSpanProcessor` as a string JSON"""
        if cls.acquire_quota():
            file_name = line_number = function_name = class_name = None

            if getattr(cls, "skip_location", False):
                if not cls.is_not_reported(cls.vulnerability_type, 0):
                    return
            else:
                file_name, line_number, function_name, class_name = cls._compute_file_line()
                if file_name is None:
                    cls.increment_quota()
                    return

            # Evidence is a string in weak cipher, weak hash and weak randomness
            result = cls._create_evidence_and_report(
                cls.vulnerability_type, evidence_value, dialect, file_name, line_number, function_name, class_name
            )
            # If result is None that's mean deduplication raises and no vulnerability wasn't reported, with that,
            # we need to restore the quota
            if not result:
                cls.increment_quota()

    @classmethod
    def is_valid_tainted(cls, string_to_check: Text) -> bool:
        """Check if a string is tainted and not marked as secure for the current vulnerability type.

        This method performs two main checks:
        1. Verifies if the string has any taint ranges (indicating it contains user-controlled data)
        2. Ensures none of these taint ranges have been marked as secure for the current vulnerability type

        A string is considered valid for vulnerability detection if:
        - It has taint ranges (indicating user input)
        - None of those ranges have been marked as secure for the current vulnerability type
        - The string is not None or empty

        Args:
            string_to_check (Text): The string to check for taint and secure marks

        Returns:
            bool: True if the string is tainted and not marked as secure, False otherwise
        """
        if len(ranges := get_ranges(string_to_check)) == 0:
            return False
        for _range in ranges:
            if not _range.has_secure_mark(cls.secure_mark):
                return True
        return True
