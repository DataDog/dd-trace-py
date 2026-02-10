from dataclasses import dataclass
import typing as t

import pytest


BENCHMARK_INFO_TAG = "benchmark.duration.info"

TAGS_TO_PYTEST_BENCHMARK_ATTRS = {
    "benchmark.duration.statistics.outliers": "outliers",
}

METRICS_TO_PYTEST_BENCHMARK_ATTRS = {
    "benchmark.duration.mean": "mean",
    "benchmark.duration.runs": "rounds",
    "benchmark.duration.statistics.hd15iqr": "hd15iqr",
    "benchmark.duration.statistics.iqr": "iqr",
    "benchmark.duration.statistics.iqr_outliers": "iqr_outliers",
    "benchmark.duration.statistics.ld15iqr": "ld15iqr",
    "benchmark.duration.statistics.max": "max",
    "benchmark.duration.statistics.mean": "mean",
    "benchmark.duration.statistics.median": "median",
    "benchmark.duration.statistics.min": "min",
    "benchmark.duration.statistics.ops": "ops",
    "benchmark.duration.statistics.q1": "q1",
    "benchmark.duration.statistics.q3": "q3",
    "benchmark.duration.statistics.n": "rounds",
    "benchmark.duration.statistics.std_dev": "stddev",
    "benchmark.duration.statistics.std_dev_outliers": "stddev_outliers",
    "benchmark.duration.statistics.total": "total",
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

    for tag_name, stats_attr in TAGS_TO_PYTEST_BENCHMARK_ATTRS.items():
        value = getattr(stats, stats_attr, None)
        if value is not None:
            data.tags[tag_name] = value

    for metric_name, stats_attr in METRICS_TO_PYTEST_BENCHMARK_ATTRS.items():
        value = getattr(stats, stats_attr, None)
        if value is not None:
            data.metrics[metric_name] = value

    return data
