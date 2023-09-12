import sys

import pytest

from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._utils import _is_python_version_supported
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.appsec._iast.taint_sinks.path_traversal import patch as path_traversal_patch
from ddtrace.appsec._iast.taint_sinks.weak_cipher import patch as weak_cipher_patch
from ddtrace.appsec._iast.taint_sinks.weak_cipher import unpatch_iast as weak_cipher_unpatch
from ddtrace.appsec._iast.taint_sinks.weak_hash import patch as weak_hash_patch
from ddtrace.appsec._iast.taint_sinks.weak_hash import unpatch_iast as weak_hash_unpatch
from ddtrace.contrib.sqlite3.patch import patch as sqli_sqlite_patch
from ddtrace.contrib.sqlite3.patch import unpatch as sqli_sqlite_unpatch
from tests.utils import override_env
from tests.utils import override_global_config


if sys.version_info >= (3, 6):
    from ddtrace.appsec._iast._patches.json_tainting import patch as json_patch
    from ddtrace.appsec._iast._patches.json_tainting import unpatch_iast as json_unpatch


def iast_span(tracer, env, request_sampling="100"):
    env.update({"DD_IAST_REQUEST_SAMPLING": request_sampling})
    VulnerabilityBase._reset_cache()
    with override_global_config(dict(_iast_enabled=True)), override_env(env):
        oce.reconfigure()
        with tracer.trace("test") as span:
            weak_hash_patch()
            weak_cipher_patch()
            path_traversal_patch()
            sqli_sqlite_patch()
            if sys.version_info >= (3, 6):
                json_patch()
            oce.acquire_request(span)
            yield span
            oce.release_request()
            weak_hash_unpatch()
            weak_cipher_unpatch()
            sqli_sqlite_unpatch()
            if sys.version_info >= (3, 6):
                json_unpatch()


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


@pytest.fixture(autouse=True)
def iast_context():
    if _is_python_version_supported():
        from ddtrace.appsec._iast._taint_tracking import create_context
        from ddtrace.appsec._iast._taint_tracking import reset_context

        _ = create_context()
        yield
        reset_context()
    else:
        yield
