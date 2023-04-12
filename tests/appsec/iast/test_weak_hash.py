import sys

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec.iast.taint_sinks.weak_hash import unpatch_iast
from ddtrace.internal import _context
from tests.appsec.iast.fixtures.weak_algorithms import hashlib_new
from tests.appsec.iast.fixtures.weak_algorithms import parametrized_week_hash


WEAK_ALGOS_FIXTURES_PATH = "tests/appsec/iast/fixtures/weak_algorithms.py"
WEAK_HASH_FIXTURES_PATH = "tests/appsec/iast/test_weak_hash.py"


@pytest.mark.parametrize(
    "hash_func,method", [("md5", "hexdigest"), ("md5", "digest"), ("sha1", "digest"), ("sha1", "hexdigest")]
)
def test_weak_hash_hashlib(iast_span_defaults, hash_func, method):
    parametrized_week_hash(hash_func, method)

    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_ALGOS_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].location.line == 14 if sys.version_info > (3, 0, 0) else 11
    assert list(span_report.vulnerabilities)[0].evidence.value == hash_func
    assert list(span_report.vulnerabilities)[0].hash == 2491892610 if sys.version_info > (3, 0, 0) else 2454487401


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="Digest is wrapped in Python 3")
@pytest.mark.parametrize("hash_func", ["md5", "sha1"])
def test_weak_hash_hashlib_no_digest(iast_span_md5_and_sha1_configured, hash_func):
    import hashlib

    m = getattr(hashlib, hash_func)()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")

    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_md5_and_sha1_configured)
    assert span_report is None


@pytest.mark.parametrize("hash_func,method", [("sha256", "digest"), ("sha256", "hexdigest")])
def test_weak_hash_secure_hash(iast_span_md5_and_sha1_configured, hash_func, method):
    import hashlib

    m = getattr(hashlib, hash_func)()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    getattr(m, method)()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_md5_and_sha1_configured)
    assert span_report is None


def test_weak_hash_new(iast_span_defaults):
    hashlib_new()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_ALGOS_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].location.line == 23 if sys.version_info > (3, 0, 0) else 20
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"
    assert list(span_report.vulnerabilities)[0].hash == 2206071529 if sys.version_info > (3, 0, 0) else 2168145072


def test_weak_hash_new_with_child_span(tracer, iast_span_defaults):
    with tracer.trace("test_child") as span:
        hashlib_new()
        span_report1 = _context.get_item(IAST.CONTEXT_KEY, span=span)

    span_report2 = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert list(span_report1.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report1.vulnerabilities)[0].location.path == WEAK_ALGOS_FIXTURES_PATH
    assert list(span_report1.vulnerabilities)[0].evidence.value == "md5"
    assert list(span_report1.vulnerabilities)[0].hash == 2206071529 if sys.version_info > (3, 0, 0) else 2168145072

    assert list(span_report2.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report2.vulnerabilities)[0].location.path == WEAK_ALGOS_FIXTURES_PATH
    assert list(span_report2.vulnerabilities)[0].evidence.value == "md5"
    assert list(span_report2.vulnerabilities)[0].hash == 2206071529 if sys.version_info > (3, 0, 0) else 2168145072


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="_md5 works only in Python 3")
def test_weak_hash_md5_builtin_py3_unpatched(iast_span_md5_and_sha1_configured):
    import _md5

    unpatch_iast()
    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_md5_and_sha1_configured)

    assert span_report is None


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="_md5 works only in Python 3")
def test_weak_hash_md5_builtin_py3_md5_and_sha1_configured(iast_span_defaults):
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"
    assert list(span_report.vulnerabilities)[0].hash == 2972079025


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="_md5 works only in Python 3")
def test_weak_hash_md5_builtin_py3_only_md4_configured(iast_span_only_md4):
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_only_md4)

    assert span_report is None


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="_md5 works only in Python 3")
def test_weak_hash_md5_builtin_py3_only_md5_configured(iast_span_only_md5):
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_only_md5)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"
    assert list(span_report.vulnerabilities)[0].hash == 2715107846


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="_md5 works only in Python 3")
def test_weak_hash_md5_builtin_py3_only_sha1_configured(iast_span_only_sha1):
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_only_sha1)

    assert span_report is None


@pytest.mark.skipif(sys.version_info > (3, 0, 0), reason="md5 works only in Python 2")
def test_weak_hash_md5_builtin_py2(iast_span_defaults):
    import md5

    m = md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"


def test_weak_hash_pycryptodome_hashes_md5(iast_span_defaults):
    from Crypto.Hash import MD5

    m = MD5.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"
    assert list(span_report.vulnerabilities)[0].hash == 758317375


def test_weak_hash_pycryptodome_hashes_sha1_defaults(iast_span_defaults):
    from Crypto.Hash import SHA1

    m = SHA1.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].evidence.value == "sha1"
    assert list(span_report.vulnerabilities)[0].hash == 3378580158


def test_weak_hash_pycryptodome_hashes_sha1_only_md5_configured(iast_span_only_md5):
    from Crypto.Hash import SHA1

    m = SHA1.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_only_md5)

    assert span_report is None


def test_weak_hash_pycryptodome_hashes_sha1_only_sha1_configured(iast_span_only_sha1):
    from Crypto.Hash import SHA1

    m = SHA1.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_only_sha1)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path == WEAK_HASH_FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].location.line == 218
    assert list(span_report.vulnerabilities)[0].evidence.value == "sha1"
    assert list(span_report.vulnerabilities)[0].hash == 1151623950


def test_weak_check_repeated(iast_span_defaults):
    import hashlib

    m = hashlib.new("md5")
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    num_vulnerabilities = 10
    for i in range(0, num_vulnerabilities):
        m.digest()

    span_report = _context.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert len(span_report.vulnerabilities) == 1
