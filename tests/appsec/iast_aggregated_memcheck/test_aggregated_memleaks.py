from tests.utils import override_env
from tests.utils import override_global_config


def test_aggregated_leaks():
    env = {"DD_IAST_ENABLED": "true", "DD_IAST_REQUEST_SAMPLING": "100"}
    with override_env(env), override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        from scripts.iast.test_leak_functions import test_iast_leaks

        assert test_iast_leaks(75000, 2.0, 100) == 0
