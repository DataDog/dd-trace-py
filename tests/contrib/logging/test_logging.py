import logging

import ddtrace
from ddtrace.compat import StringIO
from ddtrace.constants import ENV_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.contrib.logging import patch
from ddtrace.contrib.logging import unpatch
from ddtrace.contrib.logging.patch import RECORD_ATTR_SPAN_ID
from ddtrace.contrib.logging.patch import RECORD_ATTR_TRACE_ID
from ddtrace.vendor import six
from ddtrace.vendor import wrapt
from tests.utils import TracerTestCase


logger = logging.getLogger()
logger.level = logging.INFO

DEFAULT_FORMAT = (
    "%(message)s - dd.service=%(dd.service)s dd.version=%(dd.version)s dd.env=%(dd.env)s"
    " dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s"
)


def current_span(tracer=None):
    if not tracer:
        tracer = ddtrace.tracer
    return tracer.current_span()


class AssertFilter(logging.Filter):
    def filter(self, record):
        trace_id = getattr(record, RECORD_ATTR_TRACE_ID)
        assert isinstance(trace_id, six.string_types)

        span_id = getattr(record, RECORD_ATTR_SPAN_ID)
        assert isinstance(span_id, six.string_types)

        return True


def capture_function_log(func, fmt=DEFAULT_FORMAT, logger_override=None):
    if logger_override is not None:
        logger_to_capture = logger_override
    else:
        logger_to_capture = logger

    # add stream handler to capture output
    out = StringIO()
    sh = logging.StreamHandler(out)

    try:
        formatter = logging.Formatter(fmt)
        sh.setFormatter(formatter)
        logger_to_capture.addHandler(sh)
        assert_filter = AssertFilter()
        logger_to_capture.addFilter(assert_filter)
        result = func()
    finally:
        logger_to_capture.removeHandler(sh)
        logger_to_capture.removeFilter(assert_filter)

    return out.getvalue().strip(), result


class LoggingTestCase(TracerTestCase):
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
        log = logging.getLogger()
        self.assertTrue(isinstance(log.makeRecord, wrapt.BoundFunctionWrapper))

        unpatch()
        log = logging.getLogger()
        self.assertFalse(isinstance(log.makeRecord, wrapt.BoundFunctionWrapper))

    def _test_logging(self, create_span, service="", version="", env=""):
        def func():
            span = create_span()
            logger.info("Hello!")
            if span:
                span.finish()
            return span

        with self.override_config("logging", dict(tracer=self.tracer)):
            # with format string for trace info
            output, span = capture_function_log(func)
            trace_id = 0
            span_id = 0
            if span:
                trace_id = span.trace_id
                span_id = span.span_id

            assert output == "Hello! - dd.service={} dd.version={} dd.env={} dd.trace_id={} dd.span_id={}".format(
                service, version, env, trace_id, span_id
            )

            # without format string
            output, _ = capture_function_log(func, fmt="%(message)s")
            assert output == "Hello!"

    def test_log_trace(self):
        """
        Check logging patched and formatter including trace info
        """

        def create_span():
            return self.tracer.trace("test.logging")

        self._test_logging(create_span=create_span)

        with self.override_global_config(dict(version="global.version", env="global.env")):
            self._test_logging(create_span=create_span, version="global.version", env="global.env")

    def test_log_trace_service(self):
        def create_span():
            return self.tracer.trace("test.logging", service="logging")

        self._test_logging(create_span=create_span)

        with self.override_global_config(dict(version="global.version", env="global.env")):
            self._test_logging(create_span=create_span, version="global.version", env="global.env")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TAGS="service:ddtagservice,env:ddenv,version:ddversion"))
    def test_log_DD_TAGS(self):
        def create_span():
            return self.tracer.trace("test.logging")

        self._test_logging(create_span=create_span, service="ddtagservice", version="ddversion", env="ddenv")

    def test_log_trace_version(self):
        def create_span():
            span = self.tracer.trace("test.logging")
            span.set_tag(VERSION_KEY, "manual.version")
            return span

        self._test_logging(create_span=create_span, version="")

        # Setting global config version and overriding with span specific value
        # We always want the globals in the logs
        with self.override_global_config(dict(version="global.version", env="global.env")):
            self._test_logging(create_span=create_span, version="global.version", env="global.env")

    def test_log_trace_env(self):
        """
        Check logging patched and formatter including trace info
        """

        def create_span():
            span = self.tracer.trace("test.logging")
            span.set_tag(ENV_KEY, "manual.env")
            return span

        self._test_logging(create_span=create_span, env="")

        # Setting global config env and overriding with span specific value
        # We always want the globals in the logs
        with self.override_global_config(dict(version="global.version", env="global.env")):
            self._test_logging(create_span=create_span, version="global.version", env="global.env")

    def test_log_no_trace(self):
        """
        Check traced funclogging patched and formatter not including trace info
        """

        def create_span():
            return None

        self._test_logging(create_span=create_span)

        with self.override_global_config(dict(version="global.version", env="global.env")):
            self._test_logging(create_span=create_span, version="global.version", env="global.env")
