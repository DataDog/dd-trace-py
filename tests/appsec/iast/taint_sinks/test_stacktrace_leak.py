import os

from ddtrace.appsec._iast.constants import VULN_STACKTRACE_LEAK
from ddtrace.appsec._iast.taint_sinks.stacktrace_leak import asm_check_stacktrace_leak
from tests.appsec.iast.taint_sinks.conftest import _get_span_report


def _load_html_django_stacktrace():
    return open(os.path.join(os.path.dirname(__file__), "../fixtures/django_debug_page.html")).read()


def _load_text_stacktrace():
    return open(os.path.join(os.path.dirname(__file__), "../fixtures/plain_stacktrace.txt")).read()


def test_asm_check_stacktrace_leak_html(iast_context_defaults):
    asm_check_stacktrace_leak(_load_html_django_stacktrace())
    span_report = _get_span_report()
    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 1
    assert VULN_STACKTRACE_LEAK in vulnerabilities_types
    assert (
        vulnerabilities[0].evidence.value
        == 'Module: ".home.foobaruser.sources.minimal-django-example.app.py"\nException: IndexError'
    )


def test_asm_check_stacktrace_leak_text(iast_context_defaults):
    asm_check_stacktrace_leak(_load_text_stacktrace())
    span_report = _get_span_report()
    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 1
    assert VULN_STACKTRACE_LEAK in vulnerabilities_types
    assert (
        vulnerabilities[0].evidence.value
        == 'Module: ".usr.local.lib.python3.9.site-packages.constraints.py"\nException: ValueError'
    )
