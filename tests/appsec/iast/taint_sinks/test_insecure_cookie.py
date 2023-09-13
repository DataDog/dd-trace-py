import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._utils import _iast_report_to_str
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.appsec._iast.taint_sinks.insecure_cookie import asm_check_cookies
from ddtrace.internal import core


def test_insecure_cookies(iast_span_defaults):
    cookies = {"foo": "bar"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_COOKIE
    assert list(span_report.vulnerabilities)[0].evidence.value == "foo=bar"


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_nohttponly_cookies(iast_span_defaults):
    cookies = {"foo": "bar;secure"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_NO_HTTPONLY_COOKIE
    assert list(span_report.vulnerabilities)[0].evidence.value == "foo=bar;secure"
    assert list(span_report.vulnerabilities)[0].location.line is None
    assert list(span_report.vulnerabilities)[0].location.path is None
    str_report = _iast_report_to_str(span_report)
    # Double check to verify we're not sending an empty key
    assert '"line"' not in str_report
    assert '"path"' not in str_report


def test_nosamesite_cookies_missing(iast_span_defaults):
    cookies = {"foo": "bar;secure;httponly"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_NO_SAMESITE_COOKIE
    assert list(span_report.vulnerabilities)[0].evidence.value == "foo=bar;secure;httponly"


def test_nosamesite_cookies_none(iast_span_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=none"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_NO_SAMESITE_COOKIE

    assert list(span_report.vulnerabilities)[0].evidence.value == "foo=bar;secure;httponly;samesite=none"


def test_nosamesite_cookies_other(iast_span_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=none"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_NO_SAMESITE_COOKIE
    assert list(span_report.vulnerabilities)[0].evidence.value == "foo=bar;secure;httponly;samesite=none"


def test_nosamesite_cookies_lax_no_error(iast_span_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=lax"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert not span_report


def test_nosamesite_cookies_strict_no_error(iast_span_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=strict"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert not span_report
