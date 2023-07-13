import os

import pytest

import ddtrace
from ddtrace.contrib.pytest.plugin import is_enabled
from ddtrace.contrib.pytest_benchmark.constants import (
    BENCHMARK_INFO,
    BENCHMARK_MEAN,
    BENCHMARK_RUN,
    STATISTICS_HD15IQR,
    STATISTICS_IQR,
    STATISTICS_IQR_OUTLIERS,
    STATISTICS_LD15IQR,
    STATISTICS_MAX,
    STATISTICS_MEAN,
    STATISTICS_MIN,
    STATISTICS_OPS,
    STATISTICS_Q1,
    STATISTICS_Q3,
    STATISTICS_N,
    STATISTICS_STDDEV,
    STATISTICS_STDDEV_OUTLIERS,
    STATISTICS_TOTAL,
    STATISTICS_MEDIAN,
    STATISTICS_OUTLIERS,
)
from ddtrace.internal.ci_visibility import CIVisibility
from tests.ci_visibility.test_encoder import _patch_dummy_writer
from tests.utils import TracerTestCase
from tests.utils import override_env


class PytestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    def inline_run(self, *args):
        """Execute test script with test tracer."""

        class CIVisibilityPlugin:
            @staticmethod
            def pytest_configure(config):
                if is_enabled(config):
                    with _patch_dummy_writer():
                        assert CIVisibility.enabled
                        CIVisibility.disable()
                        CIVisibility.enable(tracer=self.tracer, config=ddtrace.config.pytest)

        with override_env(dict(DD_API_KEY="foobar.baz")):
            return self.testdir.inline_run(*args, plugins=[CIVisibilityPlugin()])

    def subprocess_run(self, *args):
        """Execute test script with test tracer."""
        return self.testdir.runpytest_subprocess(*args)

    def test_span_contains_benchmark(self):
        """Test with --ddtrace-patch-all."""
        py_file = self.testdir.makepyfile(
            """
            import time
            
            def sum_longer(value):
                time.sleep(2)
                return value
            def test_sum_longer(benchmark):
                result = benchmark(sum_longer, 5)
                assert result == 5
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 3

        for span in spans:
            assert span.get_tag(BENCHMARK_INFO) == "Time"
            assert span.get_metric(BENCHMARK_MEAN) > 2
            assert span.get_metric(BENCHMARK_RUN) == 5
            assert span.get_metric(STATISTICS_HD15IQR) is not None
            assert span.get_metric(STATISTICS_IQR) is not None
            assert span.get_metric(STATISTICS_IQR_OUTLIERS) is not None
            assert span.get_metric(STATISTICS_LD15IQR) is not None
            assert span.get_metric(STATISTICS_MAX) > 2
            assert span.get_metric(STATISTICS_MEAN) > 2
            assert span.get_metric(STATISTICS_MEDIAN) > 2
            assert span.get_metric(STATISTICS_MIN) > 2
            assert span.get_metric(STATISTICS_OPS) > 0
            assert span.get_tag(STATISTICS_OUTLIERS) is not None
            assert span.get_metric(STATISTICS_Q1) > 0
            assert span.get_metric(STATISTICS_Q3) > 0
            assert span.get_metric(STATISTICS_N) == 5
            assert span.get_metric(STATISTICS_STDDEV) is not None
            assert span.get_metric(STATISTICS_STDDEV_OUTLIERS) is not None
            assert span.get_metric(STATISTICS_TOTAL) > 2
            assert span.get_metric(BENCHMARK_RUN) == span.get_metric(STATISTICS_N)
            assert span.get_metric(BENCHMARK_MEAN) == span.get_metric(STATISTICS_MEAN)
