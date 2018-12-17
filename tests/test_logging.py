import unittest
import logging
import sys
import wrapt

from .base import BaseTracerTestCase
from ddtrace.utils.logs import patch_log_injection
from ddtrace import tracer

from ddtrace.span import Span
from ddtrace.context import Context, ThreadLocalContext

logger = logging.getLogger()
logger.level = logging.DEBUG


class LoggingTestCase(unittest.TestCase):
    def test_patch(self):
        """
        Confirm patching was successful
        """
        patch_log_injection()
        log = logging.getLogger()
        self.assertTrue(isinstance(log.makeRecord, wrapt.BoundFunctionWrapper))

    def test_function_trace_logged(self):
        """
        Check that a log entry from traced function includes correlation
        identifiers in log
        """

        patch_log_injection()

        # add handler and formatting to stdout
        sh = logging.StreamHandler(sys.stdout)
        fmt = '%(asctime)-15s %(name)-5s %(levelname)-8s dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s %(message)s'
        sh.setFormatter(logging.Formatter(fmt))
        logger.addHandler(sh)

        @tracer.wrap()
        def some_app_function():
            log = logging.getLogger()
            log.info('Hello!')

        try:
            some_app_function()
            # import pdb; pdb.set_trace()
            self.assertTrue(False)
        finally:
            logger.removeHandler(sh)
