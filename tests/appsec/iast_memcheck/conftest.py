import pytest

from tests.appsec.iast.conftest import iast_context


@pytest.fixture
def iast_context_defaults(tracer):
    for t in iast_context(dict(DD_IAST_ENABLED="true")):
        yield t
