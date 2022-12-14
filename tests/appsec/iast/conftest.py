import pytest

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast.taint_sinks.weak_cipher import patch as weak_cipher_patch
from ddtrace.appsec.iast.taint_sinks.weak_cipher import unpatch_iast as weak_cipher_unpatch
from ddtrace.appsec.iast.taint_sinks.weak_hash import patch as weak_hash_patch
from ddtrace.appsec.iast.taint_sinks.weak_hash import unpatch_iast as weak_hash_unpatch
from tests.utils import override_env


def iast_span(tracer, env):
    with override_env(env):
        with tracer.trace("test") as span:
            weak_hash_patch()
            weak_cipher_patch()
            oce.acquire_request()
            yield span
            oce.release_request()
            weak_hash_unpatch()
            weak_cipher_unpatch()


@pytest.fixture
def iast_span_defaults(tracer):
    for t in iast_span(tracer, dict(DD_IAST_ENABLED="true")):
        yield t


@pytest.fixture
def iast_span_des_rc2_configured(tracer):
    for t in iast_span(tracer, dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_CIPHER_ALGORITHMS="DES, RC2")):
        yield t


@pytest.fixture
def iast_span_rc4_configured(tracer):
    for t in iast_span(tracer, dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_CIPHER_ALGORITHMS="RC4")):
        yield t


@pytest.fixture
def iast_span_blowfish_configured(tracer):
    for t in iast_span(tracer, dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_CIPHER_ALGORITHMS="BLOWFISH, RC2")):
        yield t


@pytest.fixture
def iast_span_md5_and_sha1_configured(tracer):
    for t in iast_span(tracer, dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_HASH_ALGORITHMS="MD5, SHA1")):
        yield t


@pytest.fixture
def iast_span_only_md4(tracer):
    for t in iast_span(tracer, dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_HASH_ALGORITHMS="MD4")):
        yield t


@pytest.fixture
def iast_span_only_md5(tracer):
    for t in iast_span(tracer, dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_HASH_ALGORITHMS="MD5")):
        yield t


@pytest.fixture
def iast_span_only_sha1(tracer):
    for t in iast_span(tracer, dict(DD_IAST_ENABLED="true", DD_IAST_WEAK_HASH_ALGORITHMS="SHA1")):
        yield t
