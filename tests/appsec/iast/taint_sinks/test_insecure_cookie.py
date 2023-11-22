import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._utils import _iast_report_to_str
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.appsec._iast.taint_sinks.insecure_cookie import asm_check_cookies
from ddtrace.internal import core
from tests.utils import override_env


def test_insecure_cookies(iast_span_defaults):
    cookies = {"foo": "bar"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 3
    assert VULN_NO_HTTPONLY_COOKIE in vulnerabilities_types
    assert VULN_INSECURE_COOKIE in vulnerabilities_types
    assert VULN_NO_SAMESITE_COOKIE in vulnerabilities_types

    assert vulnerabilities[0].evidence.value == "foo=bar"
    assert vulnerabilities[1].evidence.value == "foo=bar"
    assert vulnerabilities[2].evidence.value == "foo=bar"

    assert vulnerabilities[0].location.line is None
    assert vulnerabilities[0].location.path is None


def test_nohttponly_cookies(iast_span_defaults):
    cookies = {"foo": "bar;secure"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 2
    assert VULN_NO_HTTPONLY_COOKIE in vulnerabilities_types
    assert VULN_NO_SAMESITE_COOKIE in vulnerabilities_types

    assert vulnerabilities[0].evidence.value == "foo=bar;secure"
    assert vulnerabilities[1].evidence.value == "foo=bar;secure"

    assert vulnerabilities[0].location.line is None
    assert vulnerabilities[0].location.path is None

    str_report = _iast_report_to_str(span_report)
    # Double check to verify we're not sending an empty key
    assert '"line"' not in str_report
    assert '"path"' not in str_report


def test_nosamesite_cookies_missing(iast_span_defaults):
    cookies = {"foo": "bar;secure;httponly"}
    asm_check_cookies(cookies)

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    vulnerabilities = list(span_report.vulnerabilities)

    assert len(vulnerabilities) == 1
    assert vulnerabilities[0].type == VULN_NO_SAMESITE_COOKIE
    assert vulnerabilities[0].evidence.value == "foo=bar;secure;httponly"


def test_nosamesite_cookies_none(iast_span_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=none"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    vulnerabilities = list(span_report.vulnerabilities)

    assert len(vulnerabilities) == 1

    assert vulnerabilities[0].type == VULN_NO_SAMESITE_COOKIE
    assert vulnerabilities[0].evidence.value == "foo=bar;secure;httponly;samesite=none"


def test_nosamesite_cookies_other(iast_span_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=none"}
    asm_check_cookies(cookies)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    vulnerabilities = list(span_report.vulnerabilities)

    assert len(vulnerabilities) == 1

    assert vulnerabilities[0].type == VULN_NO_SAMESITE_COOKIE
    assert vulnerabilities[0].evidence.value == "foo=bar;secure;httponly;samesite=none"


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


@pytest.mark.parametrize("num_vuln_expected", [3, 0, 0])
def test_insecure_cookies_deduplication(num_vuln_expected, iast_span_defaults):
    with override_env(dict(_DD_APPSEC_DEDUPLICATION_ENABLED="true")):
        cookies = {"foo": "bar"}
        asm_check_cookies(cookies)
        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
