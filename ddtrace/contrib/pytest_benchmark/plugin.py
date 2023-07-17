import pytest

from ddtrace.contrib.pytest.plugin import _extract_span
from ddtrace.contrib.pytest_benchmark.constants import BENCHMARK_INFO
from ddtrace.contrib.pytest_benchmark.constants import PLUGIN_METRICS
from ddtrace.contrib.pytest_benchmark.constants import PLUGIN_OUTLIERS

def pytest_configure(config):
    if config.pluginmanager.hasplugin("benchmark"):
        config.pluginmanager.register(_PytestBenchmarkPlugin(), "_datadog-pytest-benchmark")


class _PytestBenchmarkPlugin:
    def __init__(self):
        pass

    @pytest.hookimpl()
    def pytest_runtest_makereport(self, item, call):
        fixture_exists = hasattr(item, "funcargs") and item.funcargs.get("benchmark")
        if fixture_exists and fixture_exists.stats:
            stat_object = fixture_exists.stats.stats
            span = _extract_span(item)
            span.set_tag_str(BENCHMARK_INFO, "Time")
            for span_path, tag in PLUGIN_METRICS.items():
                if hasattr(stat_object, tag):
                    if tag == PLUGIN_OUTLIERS:
                        span.set_tag_str(span_path, getattr(stat_object, tag))
                        continue
                    span.set_tag(span_path, getattr(stat_object, tag))
