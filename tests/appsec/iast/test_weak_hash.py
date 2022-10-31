import sys

import pytest

from ddtrace.appsec.iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.constants import IAST_CONTEXT_KEY
from ddtrace.internal import _context


@pytest.mark.parametrize(
    "hash_func,method", [("md5", "digest"), ("md5", "hexdigest"), ("sha1", "digest"), ("sha1", "hexdigest")]
)
def test_weak_hash_hashlib(iast_span, hash_func, method):
    import hashlib

    m = getattr(hashlib, hash_func)()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    getattr(m, method)()
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/test_weak_hash.py")
    assert list(span_report.vulnerabilities)[0].evidence.value == hash_func


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="Digest is wrapped in Python 3")
@pytest.mark.parametrize("hash_func", ["md5", "sha1"])
def test_weak_hash_hashlib_no_digest(iast_span, hash_func):
    import hashlib

    m = getattr(hashlib, hash_func)()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")

    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert span_report is None


@pytest.mark.parametrize("hash_func,method", [("sha256", "digest"), ("sha256", "hexdigest")])
def test_weak_hash_secure_hash(iast_span, hash_func, method):
    import hashlib

    m = getattr(hashlib, hash_func)()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    getattr(m, method)()
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert span_report is None


def test_weak_hash_new(iast_span):
    import hashlib

    m = hashlib.new("md5")
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/test_weak_hash.py")
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"


def test_weak_hash_new_with_child_span(tracer, iast_span):
    import hashlib

    with tracer.trace("test_child") as span:
        m = hashlib.new("md5")
        m.update(b"Nobody inspects")
        m.update(b" the spammish repetition")
        m.digest()
        span_report1 = _context.get_item(IAST_CONTEXT_KEY, span=span)

    span_report2 = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)

    assert list(span_report1.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report1.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/test_weak_hash.py")
    assert list(span_report1.vulnerabilities)[0].evidence.value == "md5"

    assert list(span_report2.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report2.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/test_weak_hash.py")
    assert list(span_report2.vulnerabilities)[0].evidence.value == "md5"


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="_md5 works only in Python 3")
def test_weak_hash_md5_builtin_py3(iast_span):
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/test_weak_hash.py")
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"


@pytest.mark.skipif(sys.version_info > (3, 0, 0), reason="md5 works only in Python 2")
def test_weak_hash_md5_builtin_py2(iast_span):
    import md5

    m = md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/test_weak_hash.py")
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"


def test_weak_hash_pycryptodome_hashes_md5(iast_span):
    from Crypto.Hash import MD5

    m = MD5.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/test_weak_hash.py")
    assert list(span_report.vulnerabilities)[0].evidence.value == "md5"


def test_weak_hash_pycryptodome_hashes_sha1(iast_span):
    from Crypto.Hash import SHA1

    m = SHA1.new()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)

    assert list(span_report.vulnerabilities)[0].type == VULN_INSECURE_HASHING_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/test_weak_hash.py")
    assert list(span_report.vulnerabilities)[0].evidence.value == "sha1"


def test_weak_check_repeated(iast_span):
    import hashlib

    m = hashlib.new("md5")
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    num_vulnerabilities = 10
    for i in range(0, num_vulnerabilities):
        m.digest()

    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)

    assert len(span_report.vulnerabilities) == 1
