from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.appsec._iast.taint_sinks.insecure_cookie import _iast_response_cookies
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.taint_sinks.conftest import _get_span_report


def test_insecure_cookie_deduplication(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()
    for num_vuln_expected in [1, 0, 0]:
        _start_iast_context_and_oce()
        for _ in range(0, 5):
            _iast_response_cookies(
                lambda *args, **kwargs: None,
                None,
                ("insecure", "cookie"),
                dict(secure=False, httponly=True, samesite="Strict"),
            )

        span_report = _get_span_report()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
            vulnerability = list(span_report.vulnerabilities)[0]
            assert vulnerability.type == VULN_INSECURE_COOKIE
        _end_iast_context_and_oce()


def test_no_httponly_cookie_deduplication(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()
    for num_vuln_expected in [1, 0, 0]:
        _start_iast_context_and_oce()
        for _ in range(0, 5):
            _iast_response_cookies(
                lambda *args, **kwargs: None,
                None,
                ("insecure", "cookie"),
                dict(secure=True, httponly=False, samesite="Strict"),
            )

        span_report = _get_span_report()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
            vulnerability = list(span_report.vulnerabilities)[0]
            assert vulnerability.type == VULN_NO_HTTPONLY_COOKIE
        _end_iast_context_and_oce()


def test_no_samesite_cookie_deduplication(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()
    for num_vuln_expected in [1, 0, 0]:
        _start_iast_context_and_oce()
        for _ in range(0, 5):
            _iast_response_cookies(
                lambda *args, **kwargs: None,
                None,
                ("insecure", "cookie"),
                dict(secure=True, httponly=True, samesite="None"),
            )

        span_report = _get_span_report()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
            vulnerability = list(span_report.vulnerabilities)[0]
            assert vulnerability.type == VULN_NO_SAMESITE_COOKIE
        _end_iast_context_and_oce()


def test_all_cookies_deduplication(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()
    for num_vuln_expected in [3, 0, 0]:
        _start_iast_context_and_oce()
        for _ in range(0, 5):
            _iast_response_cookies(
                lambda *args, **kwargs: None,
                None,
                ("insecure", "cookie"),
                dict(secure=False, httponly=False, samesite="None"),
            )

        span_report = _get_span_report()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
            vulnerability_types = [vulnerability.type for vulnerability in span_report.vulnerabilities]
            assert VULN_NO_SAMESITE_COOKIE in vulnerability_types
            assert VULN_NO_HTTPONLY_COOKIE in vulnerability_types
            assert VULN_INSECURE_COOKIE in vulnerability_types
        _end_iast_context_and_oce()


def test_all_cookies_two_different_sinks_deduplication(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()
    for num_vuln_expected in [6, 0, 0]:
        _start_iast_context_and_oce()
        for _ in range(0, 5):
            _iast_response_cookies(
                lambda *args, **kwargs: None,
                None,
                ("insecure", "cookie"),
                dict(secure=False, httponly=False, samesite="None"),
            )
            _iast_response_cookies(
                lambda *args, **kwargs: None,
                None,
                ("insecure", "cookie"),
                dict(secure=False, httponly=False, samesite="None"),
            )

        span_report = _get_span_report()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
            vulnerability_types = [vulnerability.type for vulnerability in span_report.vulnerabilities]
            assert VULN_NO_SAMESITE_COOKIE in vulnerability_types
            assert VULN_NO_HTTPONLY_COOKIE in vulnerability_types
            assert VULN_INSECURE_COOKIE in vulnerability_types
        _end_iast_context_and_oce()


def test_all_cookies_three_different_sinks_deduplication(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()
    for num_vuln_expected in [6, 0, 0]:
        _start_iast_context_and_oce()
        for _ in range(0, 5):
            _iast_response_cookies(
                lambda *args, **kwargs: None,
                None,
                ("insecure", "cookie"),
                dict(secure=False, httponly=False, samesite="None"),
            )
            _iast_response_cookies(
                lambda *args, **kwargs: None,
                None,
                ("insecure", "cookie"),
                dict(secure=False, httponly=False, samesite="None"),
            )
            _iast_response_cookies(
                lambda *args, **kwargs: None,
                None,
                ("insecure", "cookie"),
                dict(secure=False, httponly=False, samesite="None"),
            )

        span_report = _get_span_report()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
            vulnerability_types = [vulnerability.type for vulnerability in span_report.vulnerabilities]
            assert VULN_NO_SAMESITE_COOKIE in vulnerability_types
            assert VULN_NO_HTTPONLY_COOKIE in vulnerability_types
            assert VULN_INSECURE_COOKIE in vulnerability_types
        _end_iast_context_and_oce()
