import pytest

from ddtrace.appsec._iast._taint_tracking import initialize_native_state
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_request():
    with override_global_config(
        dict(_iast_enabled=True, _iast_is_testing=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        initialize_native_state()
        assert debug_context_array_size() == 2
        _start_iast_context_and_oce()
        try:
            yield
        finally:
            _end_iast_context_and_oce()
