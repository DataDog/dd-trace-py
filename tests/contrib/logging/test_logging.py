from io import StringIO
import logging

import wrapt

import ddtrace
from ddtrace.constants import ENV_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.contrib.internal.logging.constants import RECORD_ATTR_SPAN_ID
from ddtrace.contrib.internal.logging.constants import RECORD_ATTR_TRACE_ID
from ddtrace.contrib.internal.logging.patch import patch
from ddtrace.contrib.internal.logging.patch import unpatch
from ddtrace.internal.constants import MAX_UINT_64BITS
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
        assert isinstance(trace_id, str)

        span_id = getattr(record, RECORD_ATTR_SPAN_ID)
        assert isinstance(span_id, str)

        return True


def capture_function_log(func, fmt=DEFAULT_FORMAT, logger_override=None, fmt_style=None):
    if logger_override is not None:
        logger_to_capture = logger_override
    else:
        logger_to_capture = logger

    # add stream handler to capture output
    out = StringIO()
    sh = logging.StreamHandler(out)

    try:
        if fmt_style:
            formatter = logging.Formatter(fmt, style=fmt_style)
        else:
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
        # For Python 3
        if hasattr(logging, "StrFormatStyle"):
            if hasattr(logging.StrFormatStyle, "_format"):
                assert isinstance(logging.StrFormatStyle._format, wrapt.BoundFunctionWrapper)
            else:
                assert isinstance(logging.StrFormatStyle.format, wrapt.BoundFunctionWrapper)

        unpatch()
        log = logging.getLogger()
        self.assertFalse(isinstance(log.makeRecord, wrapt.BoundFunctionWrapper))
        # For Python 3
        if hasattr(logging, "StrFormatStyle"):
            if hasattr(logging.StrFormatStyle, "_format"):
                assert not isinstance(logging.StrFormatStyle._format, wrapt.BoundFunctionWrapper)
            else:
                assert not isinstance(logging.StrFormatStyle.format, wrapt.BoundFunctionWrapper)

    def _test_logging(self, create_span, service="tests.contrib.logging", version="", env=""):
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
                trace_id = span.trace_id if span.trace_id < MAX_UINT_64BITS else "{:032x}".format(span.trace_id)
                span_id = span.span_id

            assert output.startswith(
                "Hello! - dd.service={} dd.version={} dd.env={} dd.trace_id={} dd.span_id={}".format(
                    service, version, env, trace_id, span_id
                )
            )

            # without format string
            output, _ = capture_function_log(func, fmt="%(message)s")
            assert output == "Hello!"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="False",
        )
    )
    def test_log_trace(self):
        """
        Check logging patched and formatter including trace info when 64bit trace ids are generated.
        """

        def create_span():
            return self.tracer.trace("test.logging")

        self._test_logging(create_span=create_span, service="")

        with self.override_global_config(dict(version="global.version", env="global.env")):
            self._test_logging(create_span=create_span, version="global.version", env="global.env", service="")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="True"))
    def test_log_trace_128bit_trace_ids(self):
        """
        Check if 128bit trace ids are logged using hex
        """

        def create_span():
            span = self.tracer.trace("test.logging")
            # Ensure a 128bit trace id was generated
            assert span.trace_id > MAX_UINT_64BITS
            return span

        with self.override_global_config(dict(version="v1.666", env="test")):
            self._test_logging(create_span=create_span, version="v1.666", env="test", service="")
            # makes sense that this fails because _test_logging looks for the 64 bit trace id

    def test_log_trace_service(self):
        def create_span():
            return self.tracer.trace("test.logging", service="logging")

        self._test_logging(create_span=create_span)

        with self.override_global_config(dict(version="global.version", env="global.env")):
            self._test_logging(create_span=create_span, version="global.version", env="global.env")

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_TAGS="service:ddtagservice,env:ddenv,version:ddversion", _DD_TEST_TRACE_FLUSH_ENABLED="0")
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

    def test_log_strformat_style(self):
        def func():
            with self.tracer.trace("test.logging") as span:
                logger.info("Hello!")
                return span

        fmt = (
            "{message} [dd.service={dd.service} dd.env={dd.env} "
            "dd.version={dd.version} dd.trace_id={dd.trace_id} dd.span_id={dd.span_id}]"
        )

        with self.override_config("logging", dict(tracer=self.tracer)):
            output, span = capture_function_log(func, fmt=fmt, fmt_style="{")

            lines = output.splitlines()
            assert (
                "Hello! [dd.service=tests.contrib.logging dd.env= dd.version= "
                + "dd.trace_id={:032x} dd.span_id={}]".format(span.trace_id, span.span_id)
                == lines[0]
            )

            with self.override_global_config(dict(service="my.service", version="my.version", env="my.env")):
                output, span = capture_function_log(func, fmt=fmt, fmt_style="{")

                lines = output.splitlines()
                expected = (
                    "Hello! [dd.service=my.service dd.env=my.env dd.version=my.version dd.trace_id={:032x} "
                    + "dd.span_id={}]"
                ).format(span.trace_id, span.span_id)
                assert expected == lines[0]

    def test_log_strformat_style_format(self):
        # DEV: We have to use `{msg}` instead of `{message}` because we are manually creating
        # records which does not properly configure `record.message` attribute
        fmt = (
            "{msg} [dd.service={dd.service} dd.env={dd.env} "
            "dd.version={dd.version} dd.trace_id={dd.trace_id} dd.span_id={dd.span_id}]"
        )
        formatter = logging.StrFormatStyle(fmt)

        with self.override_config("logging", dict(tracer=self.tracer)):
            with self.tracer.trace("test.logging") as span:
                record = logger.makeRecord("name", "INFO", "func", 534, "Manual log record", (), None)
                log = formatter.format(record)
                expected = (
                    "Manual log record [dd.service=tests.contrib.logging dd.env= dd.version= "
                    + "dd.trace_id={:032x} dd.span_id={}]"
                ).format(span.trace_id, span.span_id)
                assert log == expected

                assert not hasattr(record, "dd")
                assert getattr(record, RECORD_ATTR_TRACE_ID) == "{:032x}".format(span.trace_id)
                assert getattr(record, RECORD_ATTR_SPAN_ID) == str(span.span_id)
