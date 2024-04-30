import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.contrib.requests.patch import patch
from ddtrace.internal import core
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.utils import override_global_config


FIXTURES_PATH = "tests/appsec/iast/taint_sinks/test_ssrf.py"


def setup():
    oce._enabled = True


def test_ssrf(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        patch()
        import requests
        from requests.exceptions import ConnectionError

        tainted_path = taint_pyobject(
            pyobject="forbidden_dir/",
            source_name="test_ssrf",
            source_value="forbidden_dir/",
            source_origin=OriginType.PARAMETER,
        )
        url = add_aspect("http://localhost/", tainted_path)
        try:
            # label test_ssrf
            requests.get(url)
        except ConnectionError:
            pass
        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
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

        line, hash_value = get_line_and_hash("test_ssrf", VULN_SSRF, filename=FIXTURES_PATH)
        assert vulnerability["location"]["path"] == FIXTURES_PATH
        assert vulnerability["location"]["line"] == line
        assert vulnerability["hash"] == hash_value


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_ssrf_deduplication(num_vuln_expected, tracer, iast_span_deduplication_enabled):
    patch()
    import requests
    from requests.exceptions import ConnectionError

    tainted_path = taint_pyobject(
        pyobject="forbidden_dir/",
        source_name="test_ssrf",
        source_value="forbidden_dir/",
        source_origin=OriginType.PARAMETER,
    )
    url = add_aspect("http://localhost/", tainted_path)
    for _ in range(0, 5):
        try:
            # label test_ssrf
            requests.get(url)
        except ConnectionError:
            pass

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)

    if num_vuln_expected == 0:
        assert span_report is None
    else:
        assert span_report

        assert len(span_report.vulnerabilities) == num_vuln_expected
