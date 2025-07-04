import typing as t

from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class BenchmarkDurationData(t.NamedTuple):
    duration_info: t.Optional[str] = None
    duration_mean: t.Optional[float] = None
    duration_runs: t.Optional[int] = None
    statistics_hd15iqr: t.Optional[float] = None
    statistics_iqr: t.Optional[float] = None
    statistics_iqr_outliers: t.Optional[float] = None
    statistics_ld15iqr: t.Optional[float] = None
    statistics_max: t.Optional[float] = None
    statistics_mean: t.Optional[float] = None
    statistics_median: t.Optional[float] = None
    statistics_min: t.Optional[float] = None
    statistics_n: t.Optional[float] = None
    statistics_ops: t.Optional[float] = None
    statistics_outliers: t.Optional[float] = None
    statistics_q1: t.Optional[float] = None
    statistics_q3: t.Optional[float] = None
    statistics_std_dev: t.Optional[float] = None
    statistics_std_dev_outliers: t.Optional[float] = None
    statistics_total: t.Optional[float] = None


class BenchmarkTestMixin:
    @classmethod
    @_catch_and_log_exceptions
    def set_benchmark_data(
        cls,
        item_id: TestId,
        benchmark_data: t.Optional[BenchmarkDurationData] = None,
        is_benchmark: bool = True,
    ):
        log.debug("Setting benchmark data for test %s: %s", item_id, benchmark_data)
        require_ci_visibility_service().get_test_by_id(item_id).set_benchmark_data(benchmark_data, is_benchmark)


BENCHMARK_TAG_MAP = {
    "duration_info": "benchmark.duration.info",
    "duration_mean": "benchmark.duration.mean",
    "duration_runs": "benchmark.duration.runs",
    "statistics_hd15iqr": "benchmark.duration.statistics.hd15iqr",
    "statistics_iqr": "benchmark.duration.statistics.iqr",
    "statistics_iqr_outliers": "benchmark.duration.statistics.iqr_outliers",
    "statistics_ld15iqr": "benchmark.duration.statistics.ld15iqr",
    "statistics_max": "benchmark.duration.statistics.max",
    "statistics_mean": "benchmark.duration.statistics.mean",
    "statistics_median": "benchmark.duration.statistics.median",
    "statistics_min": "benchmark.duration.statistics.min",
    "statistics_n": "benchmark.duration.statistics.n",
    "statistics_ops": "benchmark.duration.statistics.ops",
    "statistics_outliers": "benchmark.duration.statistics.outliers",
    "statistics_q1": "benchmark.duration.statistics.q1",
    "statistics_q3": "benchmark.duration.statistics.q3",
    "statistics_std_dev": "benchmark.duration.statistics.std_dev",
    "statistics_std_dev_outliers": "benchmark.duration.statistics.std_dev_outliers",
    "statistics_total": "benchmark.duration.statistics.total",
}
