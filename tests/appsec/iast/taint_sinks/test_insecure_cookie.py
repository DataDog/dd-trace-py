from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.appsec._iast.taint_sinks.insecure_cookie import asm_check_cookies
from ddtrace.contrib import trace_utils
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.taint_sinks.conftest import _get_span_report
from tests.utils import override_global_config


def test_insecure_cookies(iast_context_defaults):
    cookies = {"foo": "bar"}
    asm_check_cookies(cookies)
    span_report = _get_span_report()
    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 3
    assert VULN_NO_HTTPONLY_COOKIE in vulnerabilities_types
    assert VULN_INSECURE_COOKIE in vulnerabilities_types
    assert VULN_NO_SAMESITE_COOKIE in vulnerabilities_types

    assert vulnerabilities[0].evidence.value == "foo"
    assert vulnerabilities[1].evidence.value == "foo"
    assert vulnerabilities[2].evidence.value == "foo"

    assert vulnerabilities[0].location.line is None
    assert vulnerabilities[0].location.path is None


def test_nohttponly_cookies(iast_context_defaults):
    cookies = {"foo": "bar;secure"}
    asm_check_cookies(cookies)
    span_report = _get_span_report()

    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 2
    assert VULN_NO_HTTPONLY_COOKIE in vulnerabilities_types
    assert VULN_NO_SAMESITE_COOKIE in vulnerabilities_types

    assert vulnerabilities[0].evidence.value == "foo"
    assert vulnerabilities[1].evidence.value == "foo"

    assert vulnerabilities[0].location.line is None
    assert vulnerabilities[0].location.path is None

    str_report = span_report._to_str()
    # Double check to verify we're not sending an empty key
    assert '"line"' not in str_report
    assert '"path"' not in str_report


def test_nosamesite_cookies_missing(iast_context_defaults):
    cookies = {"foo": "bar;secure;httponly"}
    asm_check_cookies(cookies)

    span_report = _get_span_report()

    vulnerabilities = list(span_report.vulnerabilities)

    assert len(vulnerabilities) == 1
    assert vulnerabilities[0].type == VULN_NO_SAMESITE_COOKIE
    assert vulnerabilities[0].evidence.value == "foo"


def test_nosamesite_cookies_none(iast_context_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=none"}
    asm_check_cookies(cookies)

    span_report = _get_span_report()

    vulnerabilities = list(span_report.vulnerabilities)

    assert len(vulnerabilities) == 1

    assert vulnerabilities[0].type == VULN_NO_SAMESITE_COOKIE
    assert vulnerabilities[0].evidence.value == "foo"


def test_nosamesite_cookies_other(iast_context_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=none"}
    asm_check_cookies(cookies)

    span_report = _get_span_report()

    vulnerabilities = list(span_report.vulnerabilities)

    assert len(vulnerabilities) == 1

    assert vulnerabilities[0].type == VULN_NO_SAMESITE_COOKIE
    assert vulnerabilities[0].evidence.value == "foo"


def test_nosamesite_cookies_lax_no_error(iast_context_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=lax"}
    asm_check_cookies(cookies)

    span_report = _get_span_report()

    assert not span_report


def test_nosamesite_cookies_strict_no_error(iast_context_defaults):
    cookies = {"foo": "bar;secure;httponly;samesite=strict"}
    asm_check_cookies(cookies)

    span_report = _get_span_report()

    assert not span_report


def test_insecure_cookies_deduplication(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()
    for num_vuln_expected in [1, 0, 0]:
        _start_iast_context_and_oce()
        cookies = {"foo": "bar"}
        asm_check_cookies(cookies)

        span_report = _get_span_report()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
        _end_iast_context_and_oce()


def test_set_http_meta_insecure_cookies_iast_disabled():
    with override_global_config(dict(_iast_enabled=False)):
        cookies = {"foo": "bar"}
        trace_utils.set_http_meta(None, None, request_cookies=cookies)
        span_report = _get_span_report()
        assert not span_report
