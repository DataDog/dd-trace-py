from _pytest.pytester import Pytester

from tests.testing.internal.pytest.utils import assert_stats


def test_assert_stats_ignores_warning_summary(pytester: Pytester) -> None:
    pytester.makepyfile(
        """
        import warnings

        def test_warns():
            warnings.warn("a harness warning", UserWarning)
        """
    )

    result = pytester.inline_run()

    assert_stats(result, passed=1)
