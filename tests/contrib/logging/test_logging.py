import logging
import wrapt

from ddtrace.compat import StringIO
from ddtrace.contrib.logging import patch, unpatch

from ...base import BaseTracerTestCase


class LoggingTestCase(BaseTracerTestCase):
    def setUp(self):
        patch()
        super(LoggingTestCase, self).setUp()

    def tearDown(self):
        unpatch()
        super(LoggingTestCase, self).tearDown()

    def test_patch(self):
        """
        Confirm patching was successful
        """
        patch()
        log = logging.getLogger()
        self.assertTrue(isinstance(log.makeRecord, wrapt.BoundFunctionWrapper))

    def test_patch_output(self):
        """
        Check that a log entry from traced function includes trace identifiers
        in log
        """
        logger = logging.getLogger()
        logger.level = logging.INFO

        @self.tracer.wrap('decorated_function', service='s', resource='r', span_type='t')
        def traced_fn():
            logger.info('Hello!')
            span = self.tracer.current_span()
            return span.trace_id, span.span_id

        def not_traced_fn():
            logger.info('Hello!')
            span = self.tracer.current_span()
            self.assertIsNone(span)

        def run_fn(fn, fmt):
            # add stream handler to capture output
            out = StringIO()
            sh = logging.StreamHandler(out)

            try:
                formatter = logging.Formatter(fmt)
                sh.setFormatter(formatter)
                logger.addHandler(sh)
                ids = fn()
            finally:
                logger.removeHandler(sh)

            return out.getvalue().strip(), ids

        # with logging patched and formatter including trace info
        output, ids = run_fn(traced_fn, fmt='%(message)s - dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s')
        self.assert_span_count(1)
        self.assertEqual(output, 'Hello! - dd.trace_id={} dd.span_id={}'.format(*ids))

        # with logging patched and formatter not including trace info
        output, _ = run_fn(traced_fn, fmt='%(message)s')
        self.assertEqual(output, 'Hello!')

        # with logging patched on an untraced function and formatter including trace info
        output, _ = run_fn(not_traced_fn, fmt='%(message)s - dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s')
        self.assertEqual(output, 'Hello! - dd.trace_id=0 dd.span_id=0')

        # logging without patching and formatter not including trace info
        unpatch()
        output, _ = run_fn(traced_fn, fmt='%(message)s')
        self.assertEqual(output, 'Hello!')
