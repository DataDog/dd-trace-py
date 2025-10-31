import pytest

from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce
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
        context_id = _start_iast_context_and_oce()
        assert context_id is not None
        try:
            yield
        finally:
            _end_iast_context_and_oce()
