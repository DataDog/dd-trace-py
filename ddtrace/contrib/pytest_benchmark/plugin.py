import pytest


def pytest_configure(config):
    if config.pluginmanager.hasplugin("benchmark"):
        config.pluginmanager.register(_PytestBenchmarkPlugin(), "_datadog-pytest-benchmark")


class _PytestBenchmarkPlugin:
    def __init__(self):
        self.benchmark_storage = {}
        pass
    def pytest_benchmark_group_stats(self, config, benchmarks, group_by):
        print(f'dd-trace-py: benchmark hook info')
        for test_benchmark in benchmarks:
            full_name = test_benchmark['fullname']
            self.benchmark_storage[test_benchmark['fullname']] = test_benchmark
            print(f'saved test: {full_name} with data {self.benchmark_storage[test_benchmark["fullname"]]}')

    @pytest.hookimpl()
    def pytest_runtest_makereport(self, item, call):
        fixture_exists = hasattr(item, "funcargs") and item.funcargs.get("benchmark")
        if fixture_exists and fixture_exists.stats:
            print(f'\nPrinting stats for {fixture_exists.stats.fullname}')
            print(f'min: {fixture_exists.stats.stats.min}')
            print(f'mean: {fixture_exists.stats.stats.mean}')
            print(f'median: {fixture_exists.stats.stats.median}')
            print(f'max: {fixture_exists.stats.stats.max}\n')

