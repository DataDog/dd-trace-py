import os
from unittest import mock

import pytest

import ddtrace
from ddtrace.contrib.pytest.plugin import is_enabled
from ddtrace.contrib.pytest_benchmark.constants import BENCHMARK_INFO
from ddtrace.contrib.pytest_benchmark.constants import BENCHMARK_MEAN
from ddtrace.contrib.pytest_benchmark.constants import BENCHMARK_RUN
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_HD15IQR
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_IQR
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_IQR_OUTLIERS
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_LD15IQR
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_MAX
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_MEAN
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_MEDIAN
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_MIN
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_N
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_OPS
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_OUTLIERS
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_Q1
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_Q3
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_STDDEV
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_STDDEV_OUTLIERS
from ddtrace.contrib.pytest_benchmark.constants import STATISTICS_TOTAL
from ddtrace.ext.test import TEST_TYPE
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
from tests.ci_visibility.test_encoder import _patch_dummy_writer
from tests.utils import TracerTestCase
from tests.utils import override_env


class PytestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    @pytest.fixture(autouse=True)
    def _dummy_check_enabled_features(self):
        """By default, assume that _check_enabled_features() returns an ITR-disabled response.

        Tests that need a different response should re-patch the CIVisibility object.
        """
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=_CIVisibilitySettings(False, False, False, False),
        ):
            yield

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
        """Test with benchmark."""
        py_file = self.testdir.makepyfile(
            """
            import time

            def sum_longer(value):
                time.sleep(0.0002)
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

        assert len(spans) == 4
        assert spans[0].get_tag(TEST_TYPE) == "benchmark"
        assert spans[0].get_tag(BENCHMARK_INFO) == "Time"

        assert isinstance(spans[0].get_metric(BENCHMARK_MEAN), float) or isinstance(
            spans[0].get_metric(BENCHMARK_MEAN), int
        )
        assert isinstance(spans[0].get_metric(BENCHMARK_RUN), int)
        assert isinstance(spans[0].get_metric(STATISTICS_HD15IQR), float) or isinstance(
            spans[0].get_metric(STATISTICS_HD15IQR), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_IQR), float) or isinstance(
            spans[0].get_metric(STATISTICS_IQR), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_IQR_OUTLIERS), int) or isinstance(
            spans[0].get_metric(STATISTICS_IQR_OUTLIERS), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_LD15IQR), float) or isinstance(
            spans[0].get_metric(STATISTICS_LD15IQR), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_MAX), float) or isinstance(
            spans[0].get_metric(STATISTICS_MAX), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_MEAN), float) or isinstance(
            spans[0].get_metric(STATISTICS_MEAN), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_MEDIAN), float) or isinstance(
            spans[0].get_metric(STATISTICS_MEDIAN), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_MIN), float) or isinstance(
            spans[0].get_metric(STATISTICS_MIN), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_OPS), float) or isinstance(
            spans[0].get_metric(STATISTICS_OPS), int
        )
        assert isinstance(spans[0].get_tag(STATISTICS_OUTLIERS), str)
        assert isinstance(spans[0].get_metric(STATISTICS_Q1), float) or isinstance(
            spans[0].get_metric(STATISTICS_Q1), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_Q3), float) or isinstance(
            spans[0].get_metric(STATISTICS_Q3), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_N), int)
        assert isinstance(spans[0].get_metric(STATISTICS_STDDEV), float) or isinstance(
            spans[0].get_metric(STATISTICS_STDDEV), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_STDDEV_OUTLIERS), float) or isinstance(
            spans[0].get_metric(STATISTICS_STDDEV_OUTLIERS), int
        )
        assert isinstance(spans[0].get_metric(STATISTICS_TOTAL), float) or isinstance(
            spans[0].get_metric(STATISTICS_TOTAL), int
        )

        assert spans[0].get_metric(BENCHMARK_MEAN) > 0.0002
        assert spans[0].get_metric(BENCHMARK_RUN) > 0
        assert spans[0].get_metric(STATISTICS_HD15IQR) is not None
        assert spans[0].get_metric(STATISTICS_IQR) is not None
        assert spans[0].get_metric(STATISTICS_IQR_OUTLIERS) is not None
        assert spans[0].get_metric(STATISTICS_LD15IQR) is not None
        assert spans[0].get_metric(STATISTICS_MAX) > 0.0002
        assert spans[0].get_metric(STATISTICS_MEAN) > 0.0002
        assert spans[0].get_metric(STATISTICS_MEDIAN) > 0.0002
        assert spans[0].get_metric(STATISTICS_MIN) > 0.0002
        assert spans[0].get_metric(STATISTICS_OPS) > 0
        assert spans[0].get_tag(STATISTICS_OUTLIERS) is not None
        assert spans[0].get_metric(STATISTICS_Q1) > 0
        assert spans[0].get_metric(STATISTICS_Q3) > 0
        assert spans[0].get_metric(STATISTICS_N) > 0
        assert spans[0].get_metric(STATISTICS_STDDEV) is not None
        assert spans[0].get_metric(STATISTICS_STDDEV_OUTLIERS) is not None
        assert spans[0].get_metric(STATISTICS_TOTAL) > 0.0002
        assert spans[0].get_metric(BENCHMARK_RUN) == spans[0].get_metric(STATISTICS_N)
        assert spans[0].get_metric(BENCHMARK_MEAN) == spans[0].get_metric(STATISTICS_MEAN)

    def test_span_no_benchmark(self):
        """Test without benchmark."""
        py_file = self.testdir.makepyfile(
            """
            import time

            def sum_longer(value):
                time.sleep(0.0002)
                return value
            def test_sum_longer():
                assert sum_longer(5) == 5
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 4
        assert spans[0].get_tag(TEST_TYPE) == "test"
        assert spans[0].get_tag(BENCHMARK_INFO) is None
        assert spans[0].get_metric(BENCHMARK_MEAN) is None
        assert spans[0].get_metric(BENCHMARK_RUN) is None
        assert spans[0].get_metric(STATISTICS_HD15IQR) is None
        assert spans[0].get_metric(STATISTICS_IQR) is None
        assert spans[0].get_metric(STATISTICS_IQR_OUTLIERS) is None
        assert spans[0].get_metric(STATISTICS_LD15IQR) is None
        assert spans[0].get_metric(STATISTICS_MAX) is None
        assert spans[0].get_metric(STATISTICS_MEAN) is None
        assert spans[0].get_metric(STATISTICS_MEDIAN) is None
        assert spans[0].get_metric(STATISTICS_MIN) is None
        assert spans[0].get_metric(STATISTICS_OPS) is None
        assert spans[0].get_tag(STATISTICS_OUTLIERS) is None
        assert spans[0].get_metric(STATISTICS_Q1) is None
        assert spans[0].get_metric(STATISTICS_Q3) is None
        assert spans[0].get_metric(STATISTICS_N) is None
        assert spans[0].get_metric(STATISTICS_STDDEV) is None
        assert spans[0].get_metric(STATISTICS_STDDEV_OUTLIERS) is None
        assert spans[0].get_metric(STATISTICS_TOTAL) is None
        assert spans[0].get_metric(BENCHMARK_RUN) == spans[0].get_metric(STATISTICS_N)
        assert spans[0].get_metric(BENCHMARK_MEAN) == spans[0].get_metric(STATISTICS_MEAN)
