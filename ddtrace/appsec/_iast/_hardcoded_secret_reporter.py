"""
Reporter for hardcoded secrets detected during AST transformation.

This module handles reporting hardcoded secrets immediately when detected,
following the same pattern as dd-trace-js and Python taint sinks.
"""

import json

from ddtrace import tracer
from ddtrace.appsec._iast._iast_request_context_base import get_iast_reporter
from ddtrace.appsec._iast._trace_utils import _asm_manual_keep
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def report_hardcoded_secret(vulnerability) -> None:
    """
    Report a hardcoded secret vulnerability immediately.

    This follows the same pattern as dd-trace-js addVulnerability:
    - If there's an active root span: attach vulnerability to it (like other sink points)
    - If no active span: create standalone trace, send immediately, and finish

    This is called from the AST visitor immediately when a secret is detected.

    Args:
        vulnerability: The vulnerability to report
    """
    try:
        # Check if there's an active root span
        span = core.get_root_span()

        if span:
            # There's an active span, attach vulnerability to it
            # This is the same pattern as other sink points (SQL injection, etc.)
            log.debug("Attaching hardcoded secret to active span %s", span.span_id)

            # Mark the span to be kept
            _asm_manual_keep(span)

            # Update vulnerability's spanId
            vulnerability.location.spanId = span.span_id

            # Get or create IAST reporter for this span
            report = get_iast_reporter()
            if report:
                report._append_vulnerability(vulnerability)
            else:
                from ddtrace.appsec._iast._iast_request_context import set_iast_reporter
                from ddtrace.appsec._iast.reporter import IastSpanReporter

                report = IastSpanReporter(vulnerabilities={vulnerability})
                report.add_ranges_to_evidence_and_extract_sources(vulnerability)
                set_iast_reporter(report)

        else:
            # No active span, create a standalone trace
            # This happens when modules are loaded at startup
            log.debug("Creating standalone trace for hardcoded secret")
            _create_standalone_vulnerability_trace(vulnerability)

    except Exception as e:
        log.debug("Error reporting hardcoded secret: %s", e, exc_info=True)


def _create_standalone_vulnerability_trace(vulnerability) -> None:
    """
    Create a standalone trace for a vulnerability.

    This creates a new trace with a single span that contains the vulnerability.
    The span is immediately finished after the vulnerability is added.

    This follows the dd-trace-js pattern:
    - span name: 'vulnerability'
    - span type: 'vulnerability'
    - immediately send and finish

    Args:
        vulnerability: The vulnerability to report
    """
    try:
        # Create a standalone span with name 'vulnerability' and type 'vulnerability'
        with tracer.trace("vulnerability", span_type="vulnerability") as span:
            # Mark the trace to be kept (similar to keepTrace in JS)
            span.set_tag(MANUAL_KEEP_KEY)

            # Set IAST enabled tag
            span.set_tag("_dd.iast.enabled", 1)

            # Update the vulnerability's spanId to match this new span
            vulnerability.location.spanId = span.span_id

            # Create a reporter for this standalone span
            from ddtrace.appsec._iast.reporter import IastSpanReporter

            standalone_reporter = IastSpanReporter()
            standalone_reporter._append_vulnerability(vulnerability)

            # Build and send the vulnerability data
            vulnerability_data = standalone_reporter.build_and_scrub_value_parts()

            if vulnerability_data:
                span.set_tag("_dd.iast.json", json.dumps(vulnerability_data))

            log.debug(
                "Created standalone vulnerability trace for %s at %s:%s",
                vulnerability.type,
                vulnerability.location.path,
                vulnerability.location.line,
            )

    except Exception as e:
        log.debug("Error creating standalone vulnerability trace: %s", e, exc_info=True)
