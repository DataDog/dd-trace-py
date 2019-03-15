import unittest

from ddtrace.runtime_metrics.metric_collectors import (
    RuntimeMetricCollector,
)


class TestRuntimeMetricCollector(unittest.TestCase):
    def test_failed_module_load_collect(self):
        """Attempts to collect from a collector when it has failed to load its
        module should return no metrics gracefully.
        """
        def collect(modules, keys):
            return {'k': 'v'}
        vc = RuntimeMetricCollector(collect_fn=collect, required_modules=['moduleshouldnotexist'])
        self.assertIsNotNone(vc.collect(), 'collect should return valid metrics')
