import unittest
import logging
import sys
import wrapt

from ddtrace import correlation
from ddtrace import tracer
from ddtrace.compat import StringIO
from ddtrace.utils.logs import patch_logging, unpatch_logging

class LoggingTestCase(unittest.TestCase):

    def setUp(self):
        patch_logging()


    def tearDown(self):
        unpatch_logging()


    def test_patch(self):
        """
        Confirm patching was successful
        """
        patch_logging()
        log = logging.getLogger()
        self.assertTrue(isinstance(log.makeRecord, wrapt.BoundFunctionWrapper))


    def test_patch_output(self):
        """
        Check that a log entry from traced function includes correlation
        identifiers in log
        """
        @tracer.wrap()
        def traced_fn():
            log = logging.getLogger()
            log.info('Hello!')
            return correlation.get_correlation_ids()

        def not_traced_fn():
            log = logging.getLogger()
            log.info('Hello!')
            return correlation.get_correlation_ids()

        def run_fn(fn):
            # add stream handler to capture output
            out = StringIO()
            sh = logging.StreamHandler(out)

            try:
                logger.addHandler(sh)
                correlation_ids = fn()
            finally:
                logger.removeHandler(sh)

            return out.getvalue().strip(), correlation_ids

        logging.basicConfig(format='%(asctime)-15s %(message)s')
        logger = logging.getLogger()
        logger.level = logging.INFO

        # with logging patched
        traced_fn_output, traced_ids = run_fn(traced_fn)
        self.assertTrue(traced_fn_output.endswith('Hello! - dd.trace_id={} dd.span_id={}'.format(*traced_ids)))

        not_traced_fn_output, not_traced_ids = run_fn(not_traced_fn)
        self.assertIsNone(not_traced_ids[0])
        self.assertIsNone(not_traced_ids[1])
        self.assertFalse('dd.trace_id=' in not_traced_fn_output)
        self.assertFalse('dd.span_id=' in not_traced_fn_output)
        self.assertTrue(not_traced_fn_output.endswith('Hello!'))

        # # logging without patching
        unpatch_logging()
        traced_fn_output, _ = run_fn(traced_fn)
        self.assertFalse('dd.trace_id=' in traced_fn_output)
        self.assertFalse('dd.span_id=' in traced_fn_output)
        self.assertTrue(traced_fn_output.endswith('Hello!'))
