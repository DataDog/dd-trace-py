import pytest

from ddtrace.appsec.iast.constants import VULN_WEAK_CIPHER_TYPE
from ddtrace.constants import IAST_CONTEXT_KEY
from ddtrace.internal import _context
from tests.appsec.iast.fixtures import cipher_arc2
from tests.appsec.iast.fixtures import cipher_arc4
from tests.appsec.iast.fixtures import cipher_blowfish
from tests.appsec.iast.fixtures import cipher_des
from tests.appsec.iast.fixtures import cryptography_algorithm


@pytest.mark.parametrize(
    "mode,cipher_func",
    [
        ("MODE_ECB", "DES_EcbMode"),
        ("MODE_CFB", "DES_CfbMode"),
        ("MODE_CBC", "DES_CbcMode"),
        ("MODE_OFB", "DES_OfbMode"),
    ],
)
def test_weak_cipher_crypto_des(iast_span, mode, cipher_func):
    from Crypto.Cipher import DES

    cipher_des(mode=getattr(DES, mode))
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert list(span_report.vulnerabilities)[0].type == VULN_WEAK_CIPHER_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/fixtures.py")
    assert list(span_report.vulnerabilities)[0].location.line == 32
    assert list(span_report.vulnerabilities)[0].evidence.value == cipher_func


@pytest.mark.parametrize(
    "mode,cipher_func",
    [
        ("MODE_ECB", "Blowfish_EcbMode"),
        ("MODE_CFB", "Blowfish_CfbMode"),
        ("MODE_CBC", "Blowfish_CbcMode"),
        ("MODE_OFB", "Blowfish_OfbMode"),
    ],
)
def test_weak_cipher_crypto_blowfish(iast_span, mode, cipher_func):
    from Crypto.Cipher import Blowfish

    cipher_blowfish(mode=getattr(Blowfish, mode))
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert list(span_report.vulnerabilities)[0].type == VULN_WEAK_CIPHER_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/fixtures.py")
    assert list(span_report.vulnerabilities)[0].location.line == 42
    assert list(span_report.vulnerabilities)[0].evidence.value == cipher_func


@pytest.mark.parametrize(
    "mode,cipher_func",
    [
        ("MODE_ECB", "RC2_EcbMode"),
        ("MODE_CFB", "RC2_CfbMode"),
        ("MODE_CBC", "RC2_CbcMode"),
        ("MODE_OFB", "RC2_OfbMode"),
    ],
)
def test_weak_cipher_rc2(iast_span, mode, cipher_func):
    from Crypto.Cipher import ARC2

    cipher_arc2(mode=getattr(ARC2, mode))
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert list(span_report.vulnerabilities)[0].type == VULN_WEAK_CIPHER_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/fixtures.py")
    assert list(span_report.vulnerabilities)[0].location.line == 52
    assert list(span_report.vulnerabilities)[0].evidence.value == cipher_func


def test_weak_cipher_rc4(iast_span):
    cipher_arc4()
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert list(span_report.vulnerabilities)[0].type == VULN_WEAK_CIPHER_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/fixtures.py")
    assert list(span_report.vulnerabilities)[0].location.line == 62
    assert list(span_report.vulnerabilities)[0].evidence.value == "RC4"


@pytest.mark.parametrize(
    "algorithm,cipher_func",
    [
        ("Blowfish", "Blowfish"),
        ("ARC4", "RC4"),
        ("IDEA", "IDEA"),
    ],
)
def test_weak_cipher_cryptography_blowfish(iast_span, algorithm, cipher_func):
    cryptography_algorithm(algorithm)
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)
    assert list(span_report.vulnerabilities)[0].type == VULN_WEAK_CIPHER_TYPE
    assert list(span_report.vulnerabilities)[0].location.path.endswith("tests/appsec/iast/fixtures.py")
    assert list(span_report.vulnerabilities)[0].location.line == 81
    assert list(span_report.vulnerabilities)[0].evidence.value == cipher_func
