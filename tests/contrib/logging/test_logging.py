import logging

import ddtrace
from ddtrace.constants import ENV_KEY, VERSION_KEY
from ddtrace.compat import StringIO
from ddtrace.contrib.logging import patch, unpatch
from ddtrace.vendor import wrapt

from ...base import BaseTracerTestCase


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
        result = func()
    finally:
        logger_to_capture.removeHandler(sh)

    return out.getvalue().strip(), result


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

    @BaseTracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_TAGS="service:ddtagservice,env:ddenv,version:ddversion")
    )
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

    def test_unfinished_child(self):
        """
        Check that closing a span with unfinished children correctly logs out
        unfinished spans.
        """
        # unfinished child spans only logged if tracer log level is debug
        self.tracer.log.setLevel(logging.DEBUG)

        # the actual logger used for these message is ddtrace.context logger,
        # so debug logging has to be enabled here as well.
        context_logger = logging.getLogger("ddtrace.context")
        context_logger.setLevel(logging.DEBUG)

        # finish parent span without finishing child span
        parent = self.tracer.trace("parent")
        child = self.tracer.trace("child")
        out, span = capture_function_log(parent.finish, logger_override=context_logger)

        assert 'Root span "parent" closed, but the trace has 1 unfinished spans' in out
        assert "parent_id {}".format(parent.span_id) in out
        assert "trace_id {}".format(child.trace_id) in out
        assert "id {}".format(child.span_id) in out
