import pytest

from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_request():
    with override_global_config(
        dict(
            _iast_enabled=True,
            _iast_is_testing=True,
            _iast_deduplication_enabled=False,
            _iast_request_sampling=100.0,
            _iast_max_concurrent_requests=100,
        )
    ):
        assert debug_context_array_size() == 2
        assert debug_context_array_free_slots_number() > 0
        context_id = _iast_start_request()
        assert context_id is not None
        try:
            yield
        finally:
            _iast_finish_request()
