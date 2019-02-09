import unittest
import sys
import mock
from ddtrace.runtime_metrics import MetricCollector


class TestMetricCollector(unittest.TestCase):
    def test_default_usage(self):
        mock_collect = mock.MagicMock()
        mock_collect.side_effect = lambda m, k: {
            'key1': 'value1',
            'key2': 'value2',
        }

        vc = MetricCollector(collect_fn=mock_collect)
        self.assertEqual(vc.collect(keys=set(['key1'])), {
            'key1': 'value1',
            'key2': 'value2',
        })
        mock_collect.assert_called_once()
        mock_collect.assert_called_with({}, set(['key1']))

        self.assertEqual(mock_collect.call_count, 1, "Collector is not periodic by default")

    def test_enabled(self):
        collect = mock.MagicMock()
        vc = MetricCollector(collect_fn=collect, enabled=False)
        collect.assert_not_called()
        vc.collect()
        collect.assert_not_called()

    def test_periodic(self):
        collect = mock.MagicMock()
        vc = MetricCollector(collect_fn=collect, periodic=True)
        vc.collect()
        self.assertEqual(collect.call_count, 1)
        vc.collect()
        self.assertEqual(collect.call_count, 2)

    def test_not_periodic(self):
        collect = mock.MagicMock()
        vc = MetricCollector(collect_fn=collect)
        collect.assert_not_called()
        vc.collect()
        self.assertEqual(collect.call_count, 1)
        vc.collect()
        self.assertEqual(collect.call_count, 1)
        vc.collect()
        self.assertEqual(collect.call_count, 1)

    def test_required_module(self):
        mock_module = mock.MagicMock()
        mock_module.fn.side_effect = lambda: 'test'
        # TODO: use a context manager to insert and delete from sys modules
        sys.modules['A'] = mock_module

        def collect(modules, keys):
            self.assertIn('A', modules)
            a = modules.get('A')
            a.fn()

        vc = MetricCollector(collect_fn=collect, required_modules=['A'])
        vc.collect()
        mock_module.fn.assert_called_once()
        del sys.modules['A']

    def test_required_module_not_installed(self):
        collect = mock.MagicMock()
        with mock.patch('ddtrace.runtime_metrics.log') as log_mock:
            # Should log a warning (tested below)
            vc = MetricCollector(collect_fn=collect, required_modules=['moduleshouldnotexist'])

            # Collect should not be called as the collector should be disabled.
            collect.assert_not_called()
            vc.collect()
            collect.assert_not_called()

        calls = [
            mock.call((
                'Could not import module "moduleshouldnotexist" for '
                '<Collector(enabled=False,periodic=False,required_modules=[\'moduleshouldnotexist\'])>'
            )
            )
        ]
        log_mock.warn.assert_has_calls(calls)

    def test_collected_values(self):
        class V(object):
            i = 0

        def collect(modules, keys):
            V.i += 1
            return {'i': V.i}

        vc = MetricCollector(collect_fn=collect)
        self.assertEqual(vc.collect(), {'i': 1})
        self.assertEqual(vc.collect(), {'i': 1})
        self.assertEqual(vc.collect(), {'i': 1})

    def test_collected_values_periodic(self):
        class V(object):
            i = 0

        def collect(modules, keys):
            V.i += 1
            return {'i': V.i}

        vc = MetricCollector(collect_fn=collect, periodic=True)
        self.assertEqual(vc.collect(), {'i': 1})
        self.assertEqual(vc.collect(), {'i': 2})
        self.assertEqual(vc.collect(), {'i': 3})

    def test_failed_module_load_collect(self):
        """Attempts to collect from a collector when it has failed to load its
        module should return no metrics gracefully.
        """
        def collect(modules, keys):
            return {'k': 'v'}
        vc = MetricCollector(collect_fn=collect, required_modules=['moduleshouldnotexist'])
        self.assertIsNotNone(vc.collect(), 'collect should return valid metrics')
