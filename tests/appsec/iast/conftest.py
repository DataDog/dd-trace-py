import pytest

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast.taint_sinks.weak_hash import patch
from ddtrace.appsec.iast.taint_sinks.weak_hash import unpatch_iast
from tests.utils import override_env


def iast_span(tracer, env):
    with override_env(env):
        with tracer.trace("test") as span:
            patch()
            oce.acquire_request()
            yield span
            oce.release_request()
            unpatch_iast()


@pytest.fixture
def iast_span_defaults(tracer):
    for t in iast_span(tracer, dict(DD_IAST_ENABLED="true")):
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
