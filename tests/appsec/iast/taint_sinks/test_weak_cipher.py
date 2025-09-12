import pytest

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._patch_modules import _testing_unpatch_iast
from ddtrace.appsec._iast.constants import VULN_WEAK_CIPHER_TYPE
from tests.appsec.iast.conftest import iast_context
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_arc2
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_arc4
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_blowfish
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_des
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cipher_secure
from tests.appsec.iast.fixtures.taint_sinks.weak_algorithms import cryptography_algorithm
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import get_line_and_hash


FIXTURES_PATH = "tests/appsec/iast/fixtures/taint_sinks/weak_algorithms.py"


@pytest.fixture
def iast_context_des_rc2_configured():
    yield from iast_context(dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_CIPHER_ALGORITHMS="DES, RC2"))


@pytest.fixture
def iast_context_rc4_configured():
    yield from iast_context(dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_CIPHER_ALGORITHMS="RC4"))


@pytest.fixture
def iast_context_blowfish_configured():
    yield from iast_context(dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_CIPHER_ALGORITHMS="BLOWFISH, RC2"))


@pytest.mark.parametrize(
    "mode,cipher_func",
    [
        ("MODE_ECB", "DES_EcbMode"),
        ("MODE_CFB", "DES_CfbMode"),
        ("MODE_CBC", "DES_CbcMode"),
        ("MODE_OFB", "DES_OfbMode"),
    ],
)
def test_weak_cipher_crypto_des(iast_context_defaults, mode, cipher_func):
    from Crypto.Cipher import DES

    cipher_des(mode=getattr(DES, mode))
    span_report = get_iast_reporter()
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
def test_weak_cipher_crypto_blowfish(iast_context_defaults, mode, cipher_func):
    from Crypto.Cipher import Blowfish

    cipher_blowfish(mode=getattr(Blowfish, mode))
    span_report = get_iast_reporter()
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
def test_weak_cipher_rc2(mode, cipher_func, iast_context_defaults):
    from Crypto.Cipher import ARC2

    cipher_arc2(mode=getattr(ARC2, mode))
    span_report = get_iast_reporter()
    line, hash_value = get_line_and_hash("cipher_arc2", VULN_WEAK_CIPHER_TYPE, filename=FIXTURES_PATH)

    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE
    assert vulnerabilities[0].location.path == FIXTURES_PATH
    assert vulnerabilities[0].location.line == line
    assert vulnerabilities[0].hash == hash_value
    assert vulnerabilities[0].evidence.value == cipher_func


def test_weak_cipher_rc4(iast_context_defaults):
    cipher_arc4()
    span_report = get_iast_reporter()
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
def test_weak_cipher_cryptography_blowfish(iast_context_defaults, algorithm, cipher_func):
    cryptography_algorithm(algorithm)
    span_report = get_iast_reporter()
    line, hash_value = get_line_and_hash("cryptography_algorithm", VULN_WEAK_CIPHER_TYPE, filename=FIXTURES_PATH)

    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE
    assert vulnerabilities[0].location.path == FIXTURES_PATH
    assert vulnerabilities[0].location.line == line
    assert vulnerabilities[0].hash == hash_value


def test_weak_cipher_blowfish__des_rc2_configured(iast_context_des_rc2_configured):
    from Crypto.Cipher import Blowfish

    cipher_blowfish(Blowfish.MODE_CBC)
    span_report = get_iast_reporter()

    assert span_report is None


def test_weak_cipher_rc2__rc4_configured(iast_context_rc4_configured):
    from Crypto.Cipher import ARC2

    cipher_arc2(mode=ARC2.MODE_CBC)
    span_report = get_iast_reporter()

    assert span_report is None


def test_weak_cipher_cryptography_rc4_configured(iast_context_rc4_configured):
    cryptography_algorithm("ARC4")
    span_report = get_iast_reporter()
    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE


def test_weak_cipher_cryptography_blowfish__rc4_configured(iast_context_rc4_configured):
    cryptography_algorithm("Blowfish")
    span_report = get_iast_reporter()
    assert span_report is None


def test_weak_cipher_cryptography_blowfish_configured(iast_context_blowfish_configured):
    cryptography_algorithm("Blowfish")
    span_report = get_iast_reporter()
    vulnerabilities = list(span_report.vulnerabilities)
    assert vulnerabilities[0].type == VULN_WEAK_CIPHER_TYPE


def test_weak_cipher_rc4_unpatched(iast_context_defaults):
    _testing_unpatch_iast()
    cipher_arc4()
    span_report = get_iast_reporter()

    assert span_report is None


def test_weak_cipher_deduplication(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()
    for num_vuln_expected in [1, 0, 0]:
        _start_iast_context_and_oce()
        for _ in range(0, 5):
            cryptography_algorithm("Blowfish")

        span_report = get_iast_reporter()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
        _end_iast_context_and_oce()


def test_weak_cipher_secure(iast_context_defaults):
    cipher_secure()
    span_report = get_iast_reporter()

    assert span_report is None


def test_weak_cipher_secure_multiple_calls_error(iast_context_defaults):
    for _ in range(50):
        cipher_secure()
    span_report = get_iast_reporter()

    assert span_report is None
