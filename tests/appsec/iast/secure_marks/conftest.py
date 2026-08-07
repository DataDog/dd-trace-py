import pytest

from ddtrace.appsec._iast._iast_request_context_base import IAST_CONTEXT
from ddtrace.appsec._iast._patch_modules import _testing_unpatch_iast
from ddtrace.appsec._iast._taint_tracking import initialize_native_state
from ddtrace.appsec._iast._taint_tracking._context import clear_all_request_context_slots
from ddtrace.appsec._iast.main import patch_iast
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_is_testing=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        initialize_native_state()
        patch_iast()
        _start_iast_context_and_oce()
        try:
            yield
        finally:
            _end_iast_context_and_oce()
            _testing_unpatch_iast()
            clear_all_request_context_slots()
            IAST_CONTEXT.set(None)
