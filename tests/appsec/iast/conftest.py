import pytest

from ddtrace._monkey import IAST_PATCH
from ddtrace._monkey import patch_iast
from ddtrace.appsec.iast import oce
from tests.utils import override_env


@pytest.fixture
def iast_span(tracer):
    with override_env(dict(DD_IAST_ENABLED="true")):
        with tracer.trace("test") as span:
            patch_iast(**IAST_PATCH)
            oce.acquire_request()
            yield span
            oce.release_request()
