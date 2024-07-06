import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.contrib.httplib.patch import patch as httplib_patch
from ddtrace.contrib.httplib.patch import unpatch as httplib_unpatch
from ddtrace.contrib.requests.patch import patch as requests_patch
from ddtrace.contrib.requests.patch import unpatch as requests_unpatch
from ddtrace.contrib.urllib.patch import patch as urllib_patch
from ddtrace.contrib.urllib.patch import unpatch as urllib_unpatch
from ddtrace.contrib.urllib3.patch import patch as urllib3_patch
from ddtrace.contrib.urllib3.patch import unpatch as urllib3_unpatch
from ddtrace.contrib.webbrowser.patch import patch as webbrowser_patch
from ddtrace.contrib.webbrowser.patch import unpatch as webbrowser_unpatch
from ddtrace.internal import core
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.utils import override_global_config


FIXTURES_PATH = "tests/appsec/iast/taint_sinks/test_ssrf.py"


def setup():
    oce._enabled = True


def _get_tainted_url():
    tainted_path = taint_pyobject(
        pyobject="forbidden_dir/",
        source_name="test_ssrf",
        source_value="forbidden_dir/",
        source_origin=OriginType.PARAMETER,
    )
    return add_aspect("http://localhost/", tainted_path), tainted_path


def _check_report(span_report, tainted_path, label):
    data = span_report.build_and_scrub_value_parts()

    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_SSRF
    assert vulnerability["evidence"]["valueParts"] == [
        {"value": "http://localhost/"},
        {"source": 0, "value": tainted_path},
    ]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None
    assert source["name"] == "test_ssrf"
    assert source["origin"] == OriginType.PARAMETER
    assert source["value"] == tainted_path

    line, hash_value = get_line_and_hash(label, VULN_SSRF, filename=FIXTURES_PATH)
    assert vulnerability["location"]["path"] == FIXTURES_PATH
    assert vulnerability["location"]["line"] == line
    assert vulnerability["hash"] == hash_value


def test_ssrf_requests(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        requests_patch()
        try:
            import requests
            from requests.exceptions import ConnectionError

            tainted_url, tainted_path = _get_tainted_url()
            try:
                # label test_ssrf_requests
                requests.get(tainted_url)
            except ConnectionError:
                pass

            span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
            assert span_report
            _check_report(span_report, tainted_path, "test_ssrf_requests")
        finally:
            requests_unpatch()


def test_ssrf_urllib3(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        urllib3_patch()
        try:
            import urllib3

            tainted_url, tainted_path = _get_tainted_url()
            try:
                # label test_ssrf_urllib3
                urllib3.request(method="GET", url=tainted_url)
            except urllib3.exceptions.HTTPError:
                pass

            span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
            assert span_report
            _check_report(span_report, tainted_path, "test_ssrf_urllib3")
        finally:
            urllib3_unpatch()


def test_ssrf_httplib(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        httplib_patch()
        try:
            import http.client

            tainted_url, tainted_path = _get_tainted_url()
            try:
                conn = http.client.HTTPConnection("localhost")
                # label test_ssrf_httplib
                conn.request("GET", tainted_url)
                conn.getresponse()
            except ConnectionError:
                pass

            span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
            assert span_report
            _check_report(span_report, tainted_path, "test_ssrf_httplib")
        finally:
            httplib_unpatch()


def test_ssrf_webbrowser(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        webbrowser_patch()
        try:
            import webbrowser

            tainted_url, tainted_path = _get_tainted_url()
            try:
                # label test_ssrf_webbrowser
                webbrowser.open(tainted_url)
            except ConnectionError:
                pass

            span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
            assert span_report
            _check_report(span_report, tainted_path, "test_ssrf_webbrowser")
        finally:
            webbrowser_unpatch()


def test_urllib_request(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        urllib_patch()
        try:
            import urllib.request

            tainted_url, tainted_path = _get_tainted_url()
            try:
                # label test_urllib_request
                urllib.request.urlopen(tainted_url)
            except urllib.error.URLError:
                pass

            span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
            assert span_report
            _check_report(span_report, tainted_path, "test_urllib_request")
        finally:
            urllib_unpatch()


def _check_no_report_if_deduplicated(span_report, num_vuln_expected):
    if num_vuln_expected == 0:
        assert span_report is None
    else:
        assert span_report

        assert len(span_report.vulnerabilities) == num_vuln_expected


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_ssrf_requests_deduplication(num_vuln_expected, tracer, iast_span_deduplication_enabled):
    requests_patch()
    try:
        import requests
        from requests.exceptions import ConnectionError

        tainted_url, tainted_path = _get_tainted_url()
        for _ in range(0, 5):
            try:
                # label test_ssrf_requests_deduplication
                requests.get(tainted_url)
            except ConnectionError:
                pass

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)
        _check_no_report_if_deduplicated(span_report, num_vuln_expected)
    finally:
        requests_unpatch()


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_ssrf_urllib3_deduplication(num_vuln_expected, tracer, iast_span_deduplication_enabled):
    urllib3_patch()
    try:
        import urllib3

        tainted_url, tainted_path = _get_tainted_url()
        for _ in range(0, 5):
            try:
                # label test_ssrf_urllib3_deduplication
                urllib3.request(method="GET", url=tainted_url)
            except urllib3.exceptions.HTTPError:
                pass

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)
        _check_no_report_if_deduplicated(span_report, num_vuln_expected)
    finally:
        requests_unpatch()


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_ssrf_httplib_deduplication(num_vuln_expected, tracer, iast_span_deduplication_enabled):
    httplib_patch()
    try:
        import http.client

        tainted_url, tainted_path = _get_tainted_url()
        for _ in range(0, 5):
            try:
                conn = http.client.HTTPConnection("localhost")
                # label test_ssrf_httplib_deduplication
                conn.request("GET", tainted_url)
                conn.getresponse()
            except ConnectionError:
                pass

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)
        _check_no_report_if_deduplicated(span_report, num_vuln_expected)
    finally:
        httplib_unpatch()


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_ssrf_webbrowser_deduplication(num_vuln_expected, tracer, iast_span_deduplication_enabled):
    webbrowser_patch()
    try:
        import webbrowser

        tainted_url, tainted_path = _get_tainted_url()
        for _ in range(0, 5):
            try:
                # label test_ssrf_webbrowser_deduplication
                webbrowser.open(tainted_url)
            except ConnectionError:
                pass

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)
        _check_no_report_if_deduplicated(span_report, num_vuln_expected)
    finally:
        webbrowser_unpatch()


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_ssrf_urllib_deduplication(num_vuln_expected, tracer, iast_span_deduplication_enabled):
    urllib_patch()
    try:
        import urllib.request

        tainted_url, tainted_path = _get_tainted_url()
        for _ in range(0, 5):
            try:
                # label test_urllib_request_deduplication
                urllib.request.urlopen(tainted_url)
            except urllib.error.URLError:
                pass

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)
        _check_no_report_if_deduplicated(span_report, num_vuln_expected)
    finally:
        urllib_unpatch()
