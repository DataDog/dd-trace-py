from tests.utils import override_env


def test_aggregated_leaks():
    with override_env({"DD_IAST_ENABLED": "True"}):
        from scripts.iast.test_leak_functions import test_iast_leaks

        assert test_iast_leaks(100000, 2.0, 100) == 0
