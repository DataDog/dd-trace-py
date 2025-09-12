import pytest

from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_is_testing=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _iast_start_request()
        try:
            yield
        finally:
            _iast_finish_request()
