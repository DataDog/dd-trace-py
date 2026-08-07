from ddtrace.appsec._iast._iast_request_context import _iast_end_request
from tests.utils import override_global_config


def test_iast_end_request_without_context_or_span():
    with override_global_config(dict(_iast_use_root_span=False)):
        _iast_end_request()
