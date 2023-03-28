from typing import TYPE_CHECKING

from ddtrace import tracer
from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._overhead_control_engine import Operation
from ddtrace.appsec.iast.reporter import Evidence
from ddtrace.appsec.iast.reporter import IastSpanReporter
from ddtrace.appsec.iast.reporter import Location
from ddtrace.appsec.iast.reporter import Vulnerability
from ddtrace.internal import _context
from ddtrace.internal.logger import get_logger


try:
    # Python >= 3.4
    from ddtrace.appsec.iast._stacktrace import get_info_frame
except ImportError:
    # Python 2
    from ddtrace.appsec.iast._stacktrace_py2 import get_info_frame

if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Text

log = get_logger(__name__)


class VulnerabilityBase(Operation):
    vulnerability_type = ""
    evidence_type = ""

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
    def report(cls, evidence_value=""):
        # type: (Text) -> None
        """Build a IastSpanReporter instance to report it in the `AppSecIastSpanProcessor` as a string JSON

        TODO: check deduplications if DD_IAST_DEDUPLICATION_ENABLED is true
        """
        if cls.acquire_quota():
            span = tracer.current_root_span()
            if not span:
                log.debug("No root span in the current execution. Skipping IAST taint sink.")
                return None

            frame_info = get_info_frame()
            if frame_info:
                file_name, line_number = frame_info
                if cls.is_not_reported(file_name, line_number):
                    report = _context.get_item(IAST.CONTEXT_KEY, span=span)
                    if report:
                        report.vulnerabilities.add(
                            Vulnerability(
                                type=cls.vulnerability_type,
                                evidence=Evidence(type=cls.evidence_type, value=evidence_value),
                                location=Location(path=file_name, line=line_number, spanId=span.span_id),
                            )
                        )

                    else:
                        report = IastSpanReporter(
                            vulnerabilities={
                                Vulnerability(
                                    type=cls.vulnerability_type,
                                    evidence=Evidence(type=cls.evidence_type, value=evidence_value),
                                    location=Location(path=file_name, line=line_number, spanId=span.span_id),
                                )
                            }
                        )
                    _context.set_item(IAST.CONTEXT_KEY, report, span=span)
