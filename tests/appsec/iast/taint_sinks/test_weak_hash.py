import sys

from mock import mock
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec._iast.taint_sinks._base import taint_sink_deduplication
from ddtrace.appsec._iast.taint_sinks.weak_hash import unpatch_iast
from ddtrace.internal import core
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import hashlib_new
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import parametrized_week_hash
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.utils import override_env


WEAK_ALGOS_FIXTURES_PATH = "tests/appsec/iast/fixtures/taint_sinks/weak_algorithms.py"
WEAK_HASH_FIXTURES_PATH = "tests/appsec/iast/taint_sinks/test_weak_hash.py"


@pytest.mark.parametrize(
    "hash_func,method",
    [
        ("md5", "hexdigest"),
        ("md5", "digest"),
        ("sha1", "digest"),
        ("sha1", "hexdigest"),
    ],
)
def test_weak_hash_hashlib(iast_span_defaults, hash_func, method):
    parametrized_week_hash(hash_func, method)

    line, hash_value = get_line_and_hash(
        "parametrized_week_hash", VULN_INSECURE_HASHING_TYPE, filename=WEAK_ALGOS_FIXTURES_PATH
    )

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_ALGOS_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].location.line == line
    assert list(span_report.vulnerabilities)[0].evidence.value == hash_func
    assert list(span_report.vulnerabilities)[0].hash == hash_value


@pytest.mark.parametrize("hash_func", ["md5", "sha1"])
def test_weak_hash_hashlib_no_digest(iast_span_md5_and_sha1_configured, hash_func):
    import hashlib

    m = getattr(hashlib, hash_func)()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_md5_and_sha1_configured)
    assert span_report is None


@pytest.mark.parametrize("hash_func,method", [("sha256", "digest"), ("sha256", "hexdigest")])
def test_weak_hash_secure_hash(iast_span_md5_and_sha1_configured, hash_func, method):
    import hashlib

    m = getattr(hashlib, hash_func)()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    getattr(m, method)()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_md5_and_sha1_configured)
    assert span_report is None


def test_weak_hash_new(iast_span_defaults):
    hashlib_new()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    line, hash_value = get_line_and_hash("hashlib_new", VULN_INSECURE_HASHING_TYPE, filename=WEAK_ALGOS_FIXTURES_PATH)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_ALGOS_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].location.line == line
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"
    assert list(span_report.vulnerabilities)[0].hash == hash_value


def test_weak_hash_new_with_child_span(tracer, iast_span_defaults):
    with tracer.trace("test_child") as span:
        hashlib_new()
        span_report1 = core.get_item(IAST.CONTEXT_KEY, span=span)

    span_report2 = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    line, hash_value = get_line_and_hash("hashlib_new", VULN_INSECURE_HASHING_TYPE, filename=WEAK_ALGOS_FIXTURES_PATH)

    assert list(span_report1.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report1.vulnerabilities)[0].location.path == WEAK_ALGOS_FIXTURES_PATH
    assert list(span_report1.vulnerabilities)[0].evidence.value == "md5"

    assert list(span_report1.vulnerabilities)[0].hash == hash_value

    assert list(span_report2.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report2.vulnerabilities)[0].location.path == WEAK_ALGOS_FIXTURES_PATH
    assert list(span_report2.vulnerabilities)[0].evidence.value == "md5"

    assert list(span_report2.vulnerabilities)[0].hash == hash_value


def test_weak_hash_md5_builtin_py3_unpatched(iast_span_md5_and_sha1_configured):
    import _md5

    unpatch_iast()
    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_md5_and_sha1_configured)

    assert span_report is None


def test_weak_hash_md5_builtin_py3_md5_and_sha1_configured(iast_span_defaults):
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"


def test_weak_hash_md5_builtin_py3_only_md4_configured(iast_span_only_md4):
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_only_md4)

    assert span_report is None


def test_weak_hash_md5_builtin_py3_only_md5_configured(iast_span_only_md5):
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_only_md5)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"


def test_weak_hash_md5_builtin_py3_only_sha1_configured(iast_span_only_sha1):
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_only_sha1)

    assert span_report is None


def test_weak_hash_pycryptodome_hashes_md5(iast_span_defaults):
    from Crypto.Hash import MD5

    m = MD5.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"


def test_weak_hash_pycryptodome_hashes_sha1_defaults(iast_span_defaults):
    from Crypto.Hash import SHA1

    m = SHA1.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "sha1"


def test_weak_hash_pycryptodome_hashes_sha1_only_md5_configured(iast_span_only_md5):
    from Crypto.Hash import SHA1

    m = SHA1.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_only_md5)

    assert span_report is None


def test_weak_hash_pycryptodome_hashes_sha1_only_sha1_configured(iast_span_only_sha1):
    from Crypto.Hash import SHA1

    m = SHA1.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_only_sha1)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "sha1"


def test_weak_check_repeated(iast_span_defaults):
    import hashlib

    m = hashlib.new("md5")
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    num_vulnerabilities = 10
    for _ in range(0, num_vulnerabilities):
        m.digest()

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert len(span_report.vulnerabilities) == 1


@pytest.mark.skipif(sys.version_info > (3, 10, 0), reason="hmac has a weak hash vulnerability until Python 3.10")
def test_weak_hash_check_hmac(iast_span_defaults):
    import hashlib
    import hmac

    mac = hmac.new(b"test", digestmod=hashlib.md5)
    mac.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert len(span_report.vulnerabilities) == 1


def test_weak_check_hmac_secure(iast_span_defaults):
    import hashlib
    import hmac

    mac = hmac.new(b"test", digestmod=hashlib.sha256)
    mac.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert span_report is None


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_weak_hash_deduplication(num_vuln_expected, iast_span_deduplication_enabled):
    import hashlib

    for _ in range(0, 5):
        m = hashlib.new("md5")
        m.digest()

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)

    if num_vuln_expected == 0:
        assert span_report is None
    else:
        assert span_report

        assert len(span_report.vulnerabilities) == num_vuln_expected


@mock.patch.object(taint_sink_deduplication, "get_last_time_reported")
def test_weak_hash_deduplication_expired_cache(mock_get_last_time_reported, iast_span_defaults):
    """CAVEAT: this test will fail at Wednesday, July 20, 5127"""
    import hashlib

    with override_env(dict(_DD_APPSEC_DEDUPLICATION_ENABLED="true")):
        mock_get_last_time_reported.return_value = 0.0
        m = hashlib.new("md5")
        m.digest()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert len(span_report.vulnerabilities) == 1

        mock_get_last_time_reported.return_value = 99642544540.0
        m = hashlib.new("md5")
        m.digest()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert len(span_report.vulnerabilities) == 1

        m = hashlib.new("md5")
        m.digest()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert len(span_report.vulnerabilities) == 1

        mock_get_last_time_reported.return_value = 1142544540.0
        m = hashlib.new("md5")
        m.digest()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert len(span_report.vulnerabilities) == 2
