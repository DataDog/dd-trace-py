from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestPytestBenchmark:
    def test_pytest_benchmark(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        pytester.makepyfile(
            test_foo="""
            import time

            def sum_longer(value):
                time.sleep(0.0002)
                return value
            def test_sum_longer(benchmark):
                result = benchmark(sum_longer, 5)
                assert result == 5
        """
        )

        # Strip outer xdist-worker env vars so the inner pytest session starts
        # cleanly and benchmark detection works correctly.
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        monkeypatch.delenv("PYTEST_XDIST_TESTRUNUID", raising=False)
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0

        test_event = event_capture.event_by_test_name("test_sum_longer")

        assert test_event["content"]["meta"].get("test.type") == "benchmark"
        assert test_event["content"]["meta"].get("benchmark.duration.info") == "Time"
        assert test_event["content"]["meta"].get("benchmark.duration.statistics.outliers") is not None

        assert test_event["content"]["metrics"].get("benchmark.duration.mean") > 0.0002
        assert test_event["content"]["metrics"].get("benchmark.duration.runs") > 0
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.hd15iqr") is not None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.iqr") is not None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.iqr_outliers") is not None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.ld15iqr") is not None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.max") > 0.0002
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.mean") > 0.0002
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.median") > 0.0002
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.min") > 0.0002
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.ops") > 0
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.q1") > 0
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.q3") > 0
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.n") > 0
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.std_dev") is not None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.std_dev_outliers") is not None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.total") > 0.0002

    def test_pytest_no_benchmark(self, pytester: Pytester) -> None:
        pytester.makepyfile(
            test_foo="""
            import time

            def sum_longer(value):
                time.sleep(0.0002)
                return value
            def test_sum_longer():
                assert sum_longer(5) == 5
        """
        )

        with (
            patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_api_client_settings()),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                result = pytester.inline_run("--ddtrace", "-v", "-s")

        assert result.ret == 0

        test_event = event_capture.event_by_test_name("test_sum_longer")
        assert test_event["content"]["meta"].get("test.type") == "test"
        assert test_event["content"]["meta"].get("benchmark.duration.info") is None
        assert test_event["content"]["meta"].get("benchmark.duration.statistics.outliers") is None

        assert test_event["content"]["metrics"].get("benchmark.duration.mean") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.runs") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.hd15iqr") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.iqr") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.iqr_outliers") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.ld15iqr") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.max") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.mean") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.median") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.min") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.ops") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.q1") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.q3") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.n") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.std_dev") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.std_dev_outliers") is None
        assert test_event["content"]["metrics"].get("benchmark.duration.statistics.total") is None
