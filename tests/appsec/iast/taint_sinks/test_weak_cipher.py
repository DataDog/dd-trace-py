import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast.constants import VULN_WEAK_CIPHER_TYPE
from ddtrace.appsec._iast.taint_sinks.weak_cipher import unpatch_iast
from ddtrace.internal import core
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_arc2
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_arc4
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_blowfish
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_des
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_secure
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cryptography_algorithm
from tests.appsec.iast.iast_utils import get_line_and_hash


FIXTURES_PATH = "tests/appsec/iast/fixtures/taint_sinks/weak_algorithms.py"


@pytest.mark.parametrize(
    "mode,cipher_func",
    [
        ("MODE_ECB", "DES_EcbMode"),
        ("MODE_CFB", "DES_CfbMode"),
        ("MODE_CBC", "DES_CbcMode"),
        ("MODE_OFB", "DES_OfbMode"),
    ],
)
def test_weak_cipher_crypto_des(iast_span_defaults, mode, cipher_func):
    from Crypto.Cipher import DES

    cipher_des(mode=getattr(DES, mode))
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    line, hash_value = get_line_and_hash("cipher_des", VULN_WEAK_CIPHER_TYPE, filename=FIXTURES_PATH)

    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE
    assert vulnerabilities[0].location.path == FIXTURES_PATH
    assert vulnerabilities[0].location.line == line
    assert vulnerabilities[0].hash == hash_value
    assert vulnerabilities[0].evidence.value == cipher_func


@pytest.mark.parametrize(
    "mode,cipher_func",
    [
        ("MODE_ECB", "Blowfish_EcbMode"),
        ("MODE_CFB", "Blowfish_CfbMode"),
        ("MODE_CBC", "Blowfish_CbcMode"),
        ("MODE_OFB", "Blowfish_OfbMode"),
    ],
)
def test_weak_cipher_crypto_blowfish(iast_span_defaults, mode, cipher_func):
    from Crypto.Cipher import Blowfish

    cipher_blowfish(mode=getattr(Blowfish, mode))
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    line, hash_value = get_line_and_hash("cipher_blowfish", VULN_WEAK_CIPHER_TYPE, filename=FIXTURES_PATH)

    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE
    assert vulnerabilities[0].location.path == FIXTURES_PATH
    assert vulnerabilities[0].location.line == line
    assert vulnerabilities[0].hash == hash_value
    assert vulnerabilities[0].evidence.value == cipher_func


@pytest.mark.parametrize(
    "mode,cipher_func",
    [
        ("MODE_ECB", "RC2_EcbMode"),
        ("MODE_CFB", "RC2_CfbMode"),
        ("MODE_CBC", "RC2_CbcMode"),
        ("MODE_OFB", "RC2_OfbMode"),
    ],
)
def test_weak_cipher_rc2(iast_span_defaults, mode, cipher_func):
    from Crypto.Cipher import ARC2

    cipher_arc2(mode=getattr(ARC2, mode))
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    line, hash_value = get_line_and_hash("cipher_arc2", VULN_WEAK_CIPHER_TYPE, filename=FIXTURES_PATH)

    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE
    assert vulnerabilities[0].location.path == FIXTURES_PATH
    assert vulnerabilities[0].location.line == line
    assert vulnerabilities[0].hash == hash_value
    assert vulnerabilities[0].evidence.value == cipher_func


def test_weak_cipher_rc4(iast_span_defaults):
    cipher_arc4()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    line, hash_value = get_line_and_hash("cipher_arc4", VULN_WEAK_CIPHER_TYPE, filename=FIXTURES_PATH)

    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE
    assert vulnerabilities[0].location.path == FIXTURES_PATH
    assert vulnerabilities[0].location.line == line
    assert vulnerabilities[0].hash == hash_value
    assert vulnerabilities[0].evidence.value == "RC4"


@pytest.mark.parametrize(
    "algorithm,cipher_func",
    [
        ("Blowfish", "blowfish"),
        ("ARC4", "rc4"),
        ("IDEA", "idea"),
    ],
)
def test_weak_cipher_cryptography_blowfish(iast_span_defaults, algorithm, cipher_func):
    cryptography_algorithm(algorithm)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    line, hash_value = get_line_and_hash("cryptography_algorithm", VULN_WEAK_CIPHER_TYPE, filename=FIXTURES_PATH)

    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE
    assert vulnerabilities[0].location.path == FIXTURES_PATH
    assert vulnerabilities[0].location.line == line
    assert vulnerabilities[0].hash == hash_value


def test_weak_cipher_blowfish__des_rc2_configured(iast_span_des_rc2_configured):
    from Crypto.Cipher import Blowfish

    cipher_blowfish(Blowfish.MODE_CBC)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_des_rc2_configured)

    assert span_report is None


def test_weak_cipher_rc2__rc4_configured(iast_span_rc4_configured):
    from Crypto.Cipher import ARC2

    cipher_arc2(mode=ARC2.MODE_CBC)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_rc4_configured)

    assert span_report is None


def test_weak_cipher_cryptography_rc4_configured(iast_span_rc4_configured):
    cryptography_algorithm("ARC4")
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_rc4_configured)
    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE


def test_weak_cipher_cryptography_blowfish__rc4_configured(iast_span_rc4_configured):
    cryptography_algorithm("Blowfish")
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_rc4_configured)
    assert span_report is None


def test_weak_cipher_cryptography_blowfish_configured(iast_span_blowfish_configured):
    cryptography_algorithm("Blowfish")
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_blowfish_configured)
    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE


def test_weak_cipher_rc4_unpatched(iast_span_defaults):
    unpatch_iast()
    cipher_arc4()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert span_report is None


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_weak_cipher_deduplication(num_vuln_expected, iast_span_deduplication_enabled):
    for _ in range(0, 5):
        cryptography_algorithm("Blowfish")

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)

    if num_vuln_expected == 0:
        assert span_report is None
    else:
        assert span_report

        assert len(span_report.vulnerabilities) == num_vuln_expected


def test_weak_cipher_secure(iast_span_defaults):
    cipher_secure()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert span_report is None


def test_weak_cipher_secure_multiple_calls_error(iast_span_defaults):
    for _ in range(50):
        cipher_secure()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert span_report is None
