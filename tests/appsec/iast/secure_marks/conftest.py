import pytest

from ddtrace.appsec._iast._patch_modules import _testing_unpatch_iast
from ddtrace.appsec._iast.main import patch_iast
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_is_testing=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        patch_iast()
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()
        _testing_unpatch_iast()
