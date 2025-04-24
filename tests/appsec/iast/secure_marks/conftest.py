import pytest

from ddtrace.appsec._iast._patch_modules import patch_iast
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(dict(_iast_enabled=True, _iast_deduplication_enabled=False, request_sampling=100.0)):
        patch_iast()
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()
