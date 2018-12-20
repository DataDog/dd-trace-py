import unittest
import logging
import wrapt

from ddtrace import correlation
from ddtrace import tracer
from ddtrace.compat import StringIO
from ddtrace.contrib.logging import patch, unpatch


class LoggingTestCase(unittest.TestCase):
    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()

    def test_patch(self):
        """
        Confirm patching was successful
        """
        patch()
        log = logging.getLogger()
        self.assertTrue(isinstance(log.makeRecord, wrapt.BoundFunctionWrapper))

    def test_patch_output(self):
        """
        Check that a log entry from traced function includes correlation
        identifiers in log
        """
        logger = logging.getLogger()
        logger.level = logging.INFO

        @tracer.wrap()
        def traced_fn():
            logger.info('Hello!')
            return correlation.get_correlation_ids()

        def not_traced_fn():
            logger.info('Hello!')
            return correlation.get_correlation_ids()

        def run_fn(fn, fmt):
            # add stream handler to capture output
            out = StringIO()
            sh = logging.StreamHandler(out)

            try:
                formatter = logging.Formatter(fmt)
                sh.setFormatter(formatter)
                logger.addHandler(sh)
                correlation_ids = fn()
            finally:
                logger.removeHandler(sh)

            return out.getvalue().strip(), correlation_ids

        # with logging patched and formatter including trace info
        output, correlation_ids = run_fn(traced_fn, fmt='%(message)s - dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s')
        self.assertEqual(output, 'Hello! - dd.trace_id={} dd.span_id={}'.format(*correlation_ids))

        # with logging patched and formatter not including trace info
        output, _ = run_fn(traced_fn, fmt='%(message)s')
        self.assertEqual(output, 'Hello!')

        # with logging patched on an untraced function and formatter including trace info
        output, correlation_ids = run_fn(not_traced_fn, fmt='%(message)s - dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s')
        self.assertIsNone(correlation_ids[0])
        self.assertIsNone(correlation_ids[1])
        self.assertEqual(output, 'Hello! - dd.trace_id=0 dd.span_id=0')

        # logging without patching and formatter not including trace info
        unpatch()
        output, _ = run_fn(traced_fn, fmt='%(message)s')
        self.assertEqual(output, 'Hello!')
