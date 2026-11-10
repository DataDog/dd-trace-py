import pytest

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.appsec._iast.taint_sinks.insecure_cookie import _iast_response_cookies
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce


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

        span_report = get_iast_reporter()

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

        span_report = get_iast_reporter()

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

        span_report = get_iast_reporter()

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

        span_report = get_iast_reporter()

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

        span_report = get_iast_reporter()

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

        span_report = get_iast_reporter()

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


@pytest.mark.parametrize("argument_style", ["positional", "keyword", "mixed"])
@pytest.mark.parametrize(
    "value, flags, expected_type",
    [
        ("token", dict(secure=False, httponly=True, samesite="Strict"), VULN_INSECURE_COOKIE),
        ("token", dict(secure=True, httponly=False, samesite="Strict"), VULN_NO_HTTPONLY_COOKIE),
        ("token", dict(secure=True, httponly=True, samesite="None"), VULN_NO_SAMESITE_COOKIE),
        ("token", dict(secure=True, httponly=True, samesite="Strict"), None),
        ("", dict(secure=False, httponly=False, samesite="None"), None),
    ],
)
def test_cookie_argument_styles(iast_context_defaults, argument_style, value, flags, expected_type):
    calls = []

    def set_cookie(key, value="", **kwargs):
        calls.append((key, value, kwargs))
        return "cookie set"

    args = ("session", value) if argument_style == "positional" else ()
    kwargs = dict(flags)
    if argument_style == "keyword":
        kwargs.update(key="session", value=value)
    elif argument_style == "mixed":
        args = ("session",)
        kwargs["value"] = value

    assert _iast_response_cookies(set_cookie, None, args, kwargs) == "cookie set"
    assert calls == [("session", value, flags)]
    report = get_iast_reporter()
    if expected_type is None:
        assert report is None
    else:
        assert report is not None
        assert {v.type for v in report.vulnerabilities} == {expected_type}
