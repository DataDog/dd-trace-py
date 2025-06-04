from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.contrib.internal.httplib.patch import patch as httplib_patch
from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch
from ddtrace.contrib.internal.requests.patch import patch as requests_patch
from ddtrace.contrib.internal.requests.patch import unpatch as requests_unpatch
from ddtrace.contrib.internal.urllib.patch import patch as urllib_patch
from ddtrace.contrib.internal.urllib.patch import unpatch as urllib_unpatch
from ddtrace.contrib.internal.urllib3.patch import patch as urllib3_patch
from ddtrace.contrib.internal.urllib3.patch import unpatch as urllib3_unpatch
from ddtrace.contrib.internal.webbrowser.patch import patch as webbrowser_patch
from ddtrace.contrib.internal.webbrowser.patch import unpatch as webbrowser_unpatch
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.appsec.iast.taint_sinks.conftest import _get_iast_data
from tests.appsec.iast.taint_sinks.conftest import _get_span_report
from tests.utils import override_global_config


FIXTURES_PATH = "tests/appsec/iast/taint_sinks/test_ssrf.py"


def _get_tainted_url():
    tainted_path = taint_pyobject(
        pyobject="forbidden_dir/",
        source_name="test_ssrf",
        source_value="forbidden_dir/",
        source_origin=OriginType.PARAMETER,
    )
    return add_aspect("http://localhost/", tainted_path), tainted_path


def _check_report(tainted_path, label):
    data = _get_iast_data()

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
    assert vulnerability["location"]["method"] == label
    assert "class" not in vulnerability["location"]
    assert type(vulnerability["location"]["spanId"]) is int
    assert vulnerability["hash"] == hash_value


def test_ssrf_requests(tracer, iast_context_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        requests_patch()
        try:
            import requests
            from requests.exceptions import ConnectionError  # noqa: A004

            tainted_url, tainted_path = _get_tainted_url()
            try:
                # label test_ssrf_requests
                requests.get(tainted_url)
            except ConnectionError:
                pass

            _check_report(tainted_path, "test_ssrf_requests")
        finally:
            requests_unpatch()


def test_ssrf_urllib3(tracer, iast_context_defaults):
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

            _check_report(tainted_path, "test_ssrf_urllib3")
        finally:
            urllib3_unpatch()


def test_ssrf_httplib(tracer, iast_context_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        httplib_patch()
        try:
            import http.client

            tainted_url, tainted_path = _get_tainted_url()
            try:
                conn = http.client.HTTPConnection("127.0.0.1")
                # label test_ssrf_httplib
                conn.request("GET", tainted_url)
                conn.getresponse()
            except ConnectionError:
                pass

            _check_report(tainted_path, "test_ssrf_httplib")
        finally:
            httplib_unpatch()


def test_ssrf_webbrowser(tracer, iast_context_defaults):
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

            _check_report(tainted_path, "test_ssrf_webbrowser")
        finally:
            webbrowser_unpatch()


def test_urllib_request(tracer, iast_context_defaults):
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

            _check_report(tainted_path, "test_urllib_request")
        finally:
            urllib_unpatch()


def _check_no_report_if_deduplicated(num_vuln_expected):
    span_report = _get_span_report()
    if num_vuln_expected == 0:
        assert span_report is None
    else:
        assert span_report

        assert len(span_report.vulnerabilities) == num_vuln_expected


def test_ssrf_requests_deduplication(iast_context_deduplication_enabled):
    requests_patch()
    try:
        import requests
        from requests.exceptions import ConnectionError  # noqa: A004

        for num_vuln_expected in [1, 0, 0]:
            _start_iast_context_and_oce()
            tainted_url, tainted_path = _get_tainted_url()
            for _ in range(0, 5):
                try:
                    # label test_ssrf_requests_deduplication
                    requests.get(tainted_url)
                except ConnectionError:
                    pass

            _check_no_report_if_deduplicated(num_vuln_expected)
            _end_iast_context_and_oce()
    finally:
        requests_unpatch()


def test_ssrf_urllib3_deduplication(iast_context_deduplication_enabled):
    urllib3_patch()
    try:
        for num_vuln_expected in [1, 0, 0]:
            _start_iast_context_and_oce()
            import urllib3

            tainted_url, tainted_path = _get_tainted_url()
            for _ in range(0, 5):
                try:
                    # label test_ssrf_urllib3_deduplication
                    urllib3.request(method="GET", url=tainted_url)
                except urllib3.exceptions.HTTPError:
                    pass

            _check_no_report_if_deduplicated(num_vuln_expected)
            _end_iast_context_and_oce()
    finally:
        requests_unpatch()


def test_ssrf_httplib_deduplication(iast_context_deduplication_enabled):
    httplib_patch()
    try:
        import http.client

        for num_vuln_expected in [1, 0, 0]:
            _start_iast_context_and_oce()
            tainted_url, tainted_path = _get_tainted_url()
            for _ in range(0, 5):
                try:
                    conn = http.client.HTTPConnection("127.0.0.1")
                    # label test_ssrf_httplib_deduplication
                    conn.request("GET", tainted_url)
                    conn.getresponse()
                except ConnectionError:
                    pass

            _check_no_report_if_deduplicated(num_vuln_expected)
            _end_iast_context_and_oce()
    finally:
        httplib_unpatch()


def test_ssrf_webbrowser_deduplication(iast_context_deduplication_enabled):
    webbrowser_patch()
    try:
        import webbrowser

        for num_vuln_expected in [1, 0, 0]:
            _start_iast_context_and_oce()
            tainted_url, tainted_path = _get_tainted_url()
            for _ in range(0, 5):
                try:
                    # label test_ssrf_webbrowser_deduplication
                    webbrowser.open(tainted_url)
                except ConnectionError:
                    pass

            _check_no_report_if_deduplicated(num_vuln_expected)
            _end_iast_context_and_oce()
    finally:
        webbrowser_unpatch()


def test_ssrf_urllib_deduplication(iast_context_deduplication_enabled):
    urllib_patch()
    try:
        import urllib.request

        for num_vuln_expected in [1, 0, 0]:
            _start_iast_context_and_oce()
            tainted_url, tainted_path = _get_tainted_url()
            for _ in range(0, 5):
                try:
                    # label test_urllib_request_deduplication
                    urllib.request.urlopen(tainted_url)
                except urllib.error.URLError:
                    pass

            _check_no_report_if_deduplicated(num_vuln_expected)
            _end_iast_context_and_oce()
    finally:
        urllib_unpatch()
