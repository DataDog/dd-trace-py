import unittest
import logging
import sys
import wrapt
from StringIO import StringIO

from .base import BaseTracerTestCase
from ddtrace.utils.logs import patch_log_injection, unpatch_log_injection
from ddtrace import tracer

logger = logging.getLogger()
logger.level = logging.DEBUG


class LoggingTestCase(unittest.TestCase):

    def setUp(self):
        patch_log_injection()


    def tearDown(self):
        unpatch_log_injection()


    def test_patch(self):
        """
        Confirm patching was successful
        """
        patch_log_injection()
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

        def not_traced_fn():
            log = logging.getLogger()
            log.info('Hello!')

        def run_fn(fn):
            out = StringIO()
            sh = logging.StreamHandler(out)

            try:
                fmt = '%(asctime)-15s %(name)-5s %(levelname)-8s dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s %(message)s'
                sh.setFormatter(logging.Formatter(fmt))
                logger.addHandler(sh)
                fn()
            finally:
                logger.removeHandler(sh)

            return out.getvalue().strip()


        # with logging patched
        traced_fn_output = run_fn(traced_fn)
        self.assertTrue('dd.trace_id=' in traced_fn_output)
        self.assertTrue('dd.span_id=' in traced_fn_output)

        not_traced_fn_output = run_fn(not_traced_fn)
        self.assertFalse('dd.trace_id=' in not_traced_fn_output)
        self.assertFalse('dd.span_id=' in not_traced_fn_output)

        # logging without patching
        unpatch_log_injection()
        traced_fn_output = run_fn(traced_fn)
        self.assertFalse('dd.trace_id=' in traced_fn_output)
        self.assertFalse('dd.span_id=' in traced_fn_output)

