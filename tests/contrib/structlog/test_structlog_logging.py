import json

import structlog
import wrapt

import ddtrace
from ddtrace.contrib.structlog import patch
from ddtrace.contrib.structlog import unpatch
from tests.utils import TracerTestCase

cf = structlog.testing.CapturingLoggerFactory()
structlog.configure(
    processors=[
        structlog.processors.JSONRenderer()
    ],
    logger_factory=cf,
)
logger = structlog.getLogger()


def current_span(tracer=None):
    if not tracer:
        tracer = ddtrace.tracer
    return tracer.current_span()


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

        structlog.configure(
            processors=[
                structlog.processors.JSONRenderer()
            ]
        )

        self.assertTrue(isinstance(structlog.configure, wrapt.BoundFunctionWrapper))

        unpatch()
        self.assertFalse(isinstance(structlog.configure, wrapt.BoundFunctionWrapper))

    def _test_logging(self, create_span, service="", version="", env=""):
        def func():
            span = create_span()
            logger.info("Hello!")
            if span:
                span.finish()
            return span

        with self.override_config("structlog", dict(tracer=self.tracer)):

            span = func()
            output = cf.logger.calls

            print(output)
            assert json.loads(output[0].args[0])["event"] == "Hello!"

    def test_log_trace(self):
        """
        Check logging patched and formatter including trace info when 64bit trace ids are generated.
        """

        def create_span():
            return self.tracer.trace("test.logging")

        self._test_logging(create_span=create_span)

        #with self.override_global_config(dict(version="global.version", env="global.env")):
        #    self._test_logging(create_span=create_span, version="global.version", env="global.env")
