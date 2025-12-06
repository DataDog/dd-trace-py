from dataclasses import dataclass
import typing as t

import pytest


BENCHMARK_INFO_TAG = "benchmark.duration.info"

PYTEST_BENCHMARK_KEYS_TO_DATADOG_TAGS = {
    "outliers": "benchmark.duration.statistics.outliers",
}

PYTEST_BENCHMARK_KEYS_TO_DATADOG_METRICS = {
    "hd15iqr": "benchmark.duration.statistics.hd15iqr",
    "iqr": "benchmark.duration.statistics.iqr",
    "iqr_outliers": "benchmark.duration.statistics.iqr_outliers",
    "ld15iqr": "benchmark.duration.statistics.ld15iqr",
    "max": "benchmark.duration.statistics.max",
    "mean": "benchmark.duration.statistics.mean",
    "median": "benchmark.duration.statistics.median",
    "min": "benchmark.duration.statistics.min",
    "ops": "benchmark.duration.statistics.ops",
    "q1": "benchmark.duration.statistics.q1",
    "q3": "benchmark.duration.statistics.q3",
    "rounds": "benchmark.duration.statistics.n",
    "stddev": "benchmark.duration.statistics.std_dev",
    "stddev_outliers": "benchmark.duration.statistics.std_dev_outliers",
    "total": "benchmark.duration.statistics.total",
}


@dataclass
class BenchmarkData:
    tags: t.Dict[str, str]
    metrics: t.Dict[str, float]


def get_benchmark_tags_and_metrics(item: pytest.Item) -> t.Optional[BenchmarkData]:
    if not item.config.pluginmanager.hasplugin("benchmark"):
        return None

    funcargs = getattr(item, "funcargs", None)
    if not funcargs:
        return None

    benchmark_fixture = item.funcargs.get("benchmark")
    if not benchmark_fixture or not benchmark_fixture.stats:
        return None

    stats = item.funcargs.get("benchmark").stats.stats

    data = BenchmarkData(tags={}, metrics={})
    data.tags[BENCHMARK_INFO_TAG] = "Time"

    for stats_attr, tag_name in PYTEST_BENCHMARK_KEYS_TO_DATADOG_TAGS.items():
        value = getattr(stats, stats_attr, None)
        if value is not None:
            data.tags[tag_name] = value

    for stats_attr, metric_name in PYTEST_BENCHMARK_KEYS_TO_DATADOG_METRICS.items():
        value = getattr(stats, stats_attr, None)
        if value is not None:
            data.metrics[metric_name] = value

    return data
