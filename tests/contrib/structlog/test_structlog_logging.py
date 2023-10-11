import json

import structlog
import wrapt

import ddtrace
from ddtrace.constants import ENV_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.contrib.structlog import patch
from ddtrace.contrib.structlog import unpatch
from ddtrace.internal.constants import MAX_UINT_64BITS
from tests.utils import TracerTestCase


def current_span(tracer=None):
    if not tracer:
        tracer = ddtrace.tracer
    return tracer.current_span()


class StructLogTestCase(TracerTestCase):
    def setUp(self):
        patch()
        self.cf = structlog.testing.CapturingLoggerFactory()
        structlog.configure(
            processors=[structlog.processors.JSONRenderer()],
            logger_factory=self.cf,
        )
        self.logger = structlog.getLogger()
        super(StructLogTestCase, self).setUp()

    def tearDown(self):
        unpatch()
        super(StructLogTestCase, self).tearDown()

    def test_patch(self):
        """
        Confirm patching was successful
        """

        self.assertTrue(isinstance(structlog.configure, wrapt.FunctionWrapper))

        unpatch()
        self.assertFalse(isinstance(structlog.configure, wrapt.FunctionWrapper))

    def _test_logging(self, create_span, service="", version="", env=""):
        def func():
            span = create_span()
            self.logger.info("Hello!")
            if span:
                span.finish()
            return span

        with self.override_config("structlog", dict(tracer=self.tracer)):
            span = func()
            output = self.cf.logger.calls

            dd_trace_id, dd_span_id = (span.trace_id, span.span_id) if span else (0, 0)

            assert json.loads(output[0].args[0])["event"] == "Hello!"
            assert json.loads(output[0].args[0])["dd.trace_id"] == str(dd_trace_id)
            assert json.loads(output[0].args[0])["dd.span_id"] == str(dd_span_id)
            assert json.loads(output[0].args[0])["dd.env"] == env or ""
            assert json.loads(output[0].args[0])["dd.service"] == service or ""
            assert json.loads(output[0].args[0])["dd.version"] == version or ""

            self.cf.logger.calls.clear()

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

        self._test_logging(create_span=create_span)

        with self.override_global_config(dict(version="global.version", env="global.env")):
            self._test_logging(create_span=create_span, version="global.version", env="global.env")

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="True", DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED="True"
        )
    )
    def test_log_trace_128bit_trace_ids(self):
        """
        Check if 128bit trace ids are logged when `DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED=True`
        """

        def create_span():
            span = self.tracer.trace("test.logging")
            # Ensure a 128bit trace id was generated
            assert span.trace_id > MAX_UINT_64BITS
            return span

        with self.override_global_config(dict(version="v1.666", env="test")):
            self._test_logging(create_span=create_span, version="v1.666", env="test")

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="True", DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED="False"
        )
    )
    def test_log_trace_128bit_trace_ids_log_64bits(self):
        """
        Check if a 64 bit trace trace id is logged when `DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED=False`
        """

        def generate_log_in_span():
            with self.tracer.trace("test.logging") as span:
                self.logger.info("Hello!")
            return span

        with self.override_config("structlog", dict(tracer=self.tracer)):
            span = generate_log_in_span()
            output = self.cf.logger.calls

            assert span.trace_id > MAX_UINT_64BITS

            dd_trace_id, dd_span_id = (span._trace_id_64bits, span.span_id) if span else (0, 0)

            assert json.loads(output[0].args[0])["event"] == "Hello!"
            assert json.loads(output[0].args[0])["dd.trace_id"] == str(dd_trace_id)
            assert json.loads(output[0].args[0])["dd.span_id"] == str(dd_span_id)
            assert json.loads(output[0].args[0])["dd.env"] == ""
            assert json.loads(output[0].args[0])["dd.service"] == ""
            assert json.loads(output[0].args[0])["dd.version"] == ""

            self.cf.logger.calls.clear()

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

    def test_no_processors(self):
        # Ensure nothing is injected if there is no valid rendering in processors
        structlog.configure(processors=[], logger_factory=self.cf)
        self.logger = structlog.getLogger()

        def func():
            span = self.tracer.trace("test.logging")
            self.logger.info("Hello!")
            if span:
                span.finish()
            return span

        with self.override_config("structlog", dict(tracer=self.tracer)):
            func()
            output = self.cf.logger.calls

            assert output[0].kwargs["event"] == "Hello!"
            assert "dd.trace_id" not in output[0].kwargs
            assert "dd.span_id" not in output[0].kwargs
            assert "dd.env" not in output[0].kwargs
            assert "dd.service" not in output[0].kwargs
            assert "dd.version" not in output[0].kwargs

            self.cf.logger.calls.clear()
