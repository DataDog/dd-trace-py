import pytest

from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_create_context():
    env = {"DD_IAST_REQUEST_SAMPLING": "100"}
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)), override_env(env):
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()
