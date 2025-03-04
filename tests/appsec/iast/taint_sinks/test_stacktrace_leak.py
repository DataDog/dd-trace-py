import os

from ddtrace.appsec._iast.constants import VULN_STACKTRACE_LEAK
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.appsec._iast.taint_sinks.stacktrace_leak import check_and_report_stacktrace_leak
from ddtrace.appsec._iast.taint_sinks.stacktrace_leak import get_report_stacktrace_later
from ddtrace.appsec._iast.taint_sinks.stacktrace_leak import iast_check_stacktrace_leak
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.taint_sinks.conftest import _get_span_report


def _load_html_django_stacktrace():
    return open(os.path.join(os.path.dirname(__file__), "../fixtures/django_debug_page.html")).read()


def _load_text_stacktrace():
    return open(os.path.join(os.path.dirname(__file__), "../fixtures/plain_stacktrace.txt")).read()


def test_check_stacktrace_leak_html(iast_context_defaults):
    iast_check_stacktrace_leak(_load_html_django_stacktrace())
    span_report = _get_span_report()
    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 1
    assert VULN_STACKTRACE_LEAK in vulnerabilities_types
    assert (
        vulnerabilities[0].evidence.value
        == 'Module: ".home.foobaruser.sources.minimal-django-example.app.py"\nException: IndexError'
    )


def test_check_stacktrace_leak_text(iast_context_defaults):
    iast_check_stacktrace_leak(_load_text_stacktrace())
    span_report = _get_span_report()
    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 1
    assert VULN_STACKTRACE_LEAK in vulnerabilities_types
    assert (
        vulnerabilities[0].evidence.value
        == 'Module: ".usr.local.lib.python3.9.site-packages.constraints.py"\nException: ValueError'
    )
    VulnerabilityBase._prepare_report._reset_cache()


def test_stacktrace_leak_deduplication(iast_context_deduplication_enabled):
    for num_vuln_expected in [1, 0, 0]:
        _start_iast_context_and_oce()
        for _ in range(0, 5):
            iast_check_stacktrace_leak(_load_text_stacktrace())

        span_report = _get_span_report()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
            vulnerability = list(span_report.vulnerabilities)[0]
            assert vulnerability.type == VULN_STACKTRACE_LEAK
        _end_iast_context_and_oce()
    VulnerabilityBase._prepare_report._reset_cache()


def test_check_stacktrace_leak_text_outside_context(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()

    # Report stacktrace outside the context
    iast_check_stacktrace_leak(_load_text_stacktrace())
    assert get_report_stacktrace_later() is not None
    _start_iast_context_and_oce()

    # Check the stacktrace, now with a context, like the beginning of a request
    check_and_report_stacktrace_leak()
    span_report = _get_span_report()
    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 1
    assert VULN_STACKTRACE_LEAK in vulnerabilities_types
    assert (
        vulnerabilities[0].evidence.value
        == 'Module: ".usr.local.lib.python3.9.site-packages.constraints.py"\nException: ValueError'
    )
    assert get_report_stacktrace_later() is None
    _end_iast_context_and_oce()
