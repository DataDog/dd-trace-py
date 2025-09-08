import pytest

from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.appsec._iast._patch_modules import _testing_unpatch_iast
from ddtrace.appsec._iast.main import patch_iast
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_is_testing=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        patch_iast()
        _iast_start_request()
        yield
        _iast_finish_request()
        _testing_unpatch_iast()
