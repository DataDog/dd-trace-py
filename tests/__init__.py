import contextlib
import sys

import ddtrace
from ddtrace import Tracer
from ddtrace.encoding import JSONEncoder
from ddtrace.internal._encoding import MsgpackEncoder
from ddtrace.internal.writer import AgentWriter
from tests import TestSpan
from tests.subprocesstest import SubprocessTestCase
from tests.utils import override_env, assert_is_measured, assert_is_not_measured
from tests.utils.span import TestSpanContainer, TestSpan, NO_CHILDREN


class BaseTestCase(SubprocessTestCase):
    """
    BaseTestCase extends ``unittest.TestCase`` to provide some useful helpers/assertions


    Example::

        from tests import BaseTestCase


        class MyTestCase(BaseTestCase):
            def test_case(self):
                with self.override_config('flask', dict(distributed_tracing_enabled=True):
                    pass
    """

    # Expose `override_env` as `self.override_env`
    override_env = staticmethod(override_env)

    assert_is_measured = staticmethod(assert_is_measured)

    assert_is_not_measured = staticmethod(assert_is_not_measured)

    @staticmethod
    @contextlib.contextmanager
    def override_global_config(values):
        """
        Temporarily override an global configuration::

            >>> with self.override_global_config(dict(name=value,...)):
                # Your test
        """
        # List of global variables we allow overriding
        # DEV: We do not do `ddtrace.config.keys()` because we have all of our integrations
        global_config_keys = [
            "analytics_enabled",
            "report_hostname",
            "health_metrics_enabled",
            "env",
            "version",
            "service",
        ]

        # Grab the current values of all keys
        originals = dict(
            (key, getattr(ddtrace.config, key)) for key in global_config_keys
        )

        # Override from the passed in keys
        for key, value in values.items():
            if key in global_config_keys:
                setattr(ddtrace.config, key, value)
        try:
            yield
        finally:
            # Reset all to their original values
            for key, value in originals.items():
                setattr(ddtrace.config, key, value)

    @staticmethod
    @contextlib.contextmanager
    def override_config(integration, values):
        """
        Temporarily override an integration configuration value::

            >>> with self.override_config('flask', dict(service_name='test-service')):
                # Your test
        """
        options = getattr(ddtrace.config, integration)

        original = dict(
            (key, options.get(key))
            for key in values.keys()
        )

        options.update(values)
        try:
            yield
        finally:
            options.update(original)

    @staticmethod
    @contextlib.contextmanager
    def override_http_config(integration, values):
        """
        Temporarily override an integration configuration for HTTP value::

            >>> with self.override_http_config('flask', dict(trace_query_string=True)):
                # Your test
        """
        options = getattr(ddtrace.config, integration).http

        original = {}
        for key, value in values.items():
            original[key] = getattr(options, key)
            setattr(options, key, value)

        try:
            yield
        finally:
            for key, value in original.items():
                setattr(options, key, value)

    @staticmethod
    @contextlib.contextmanager
    def override_sys_modules(modules):
        """
        Temporarily override ``sys.modules`` with provided dictionary of modules::

            >>> mock_module = mock.MagicMock()
            >>> mock_module.fn.side_effect = lambda: 'test'
            >>> with self.override_sys_modules(dict(A=mock_module)):
                # Your test
        """
        original = dict(sys.modules)

        sys.modules.update(modules)
        try:
            yield
        finally:
            sys.modules.clear()
            sys.modules.update(original)


class TracerTestCase(TestSpanContainer, BaseTestCase):
    """
    BaseTracerTestCase is a base test case for when you need access to a dummy tracer and span assertions
    """
    def setUp(self):
        """Before each test case, setup a dummy tracer to use"""
        self.tracer = DummyTracer()

        super(TracerTestCase, self).setUp()

    def tearDown(self):
        """After each test case, reset and remove the dummy tracer"""
        super(TracerTestCase, self).tearDown()

        self.reset()
        delattr(self, 'tracer')

    def get_spans(self):
        """Required subclass method for TestSpanContainer"""
        return self.tracer.writer.spans

    def reset(self):
        """Helper to reset the existing list of spans created"""
        self.tracer.writer.pop()

    def trace(self, *args, **kwargs):
        """Wrapper for self.tracer.trace that returns a TestSpan"""
        return TestSpan(self.tracer.trace(*args, **kwargs))

    def start_span(self, *args, **kwargs):
        """Helper for self.tracer.start_span that returns a TestSpan"""
        return TestSpan(self.tracer.start_span(*args, **kwargs))

    def assert_structure(self, root, children=NO_CHILDREN):
        """Helper to call TestSpanNode.assert_structure on the current root span"""
        root_span = self.get_root_span()
        root_span.assert_structure(root, children)

    @contextlib.contextmanager
    def override_global_tracer(self, tracer=None):
        original = ddtrace.tracer
        tracer = tracer or self.tracer
        setattr(ddtrace, 'tracer', tracer)
        try:
            yield
        finally:
            setattr(ddtrace, 'tracer', original)


class DummyWriter(AgentWriter):
    """DummyWriter is a small fake writer used for tests. not thread-safe."""

    def __init__(self, *args, **kwargs):
        # original call
        super(DummyWriter, self).__init__(*args, **kwargs)

        # dummy components
        self.spans = []
        self.traces = []
        self.services = {}
        self.json_encoder = JSONEncoder()
        self.msgpack_encoder = MsgpackEncoder()

    def write(self, spans=None, services=None):
        if spans:
            # the traces encoding expect a list of traces so we
            # put spans in a list like we do in the real execution path
            # with both encoders
            trace = [spans]
            self.json_encoder.encode_traces(trace)
            self.msgpack_encoder.encode_traces(trace)
            self.spans += spans
            self.traces += trace

        if services:
            self.json_encoder.encode_services(services)
            self.msgpack_encoder.encode_services(services)
            self.services.update(services)

    def pop(self):
        # dummy method
        s = self.spans
        self.spans = []
        return s

    def pop_traces(self):
        # dummy method
        traces = self.traces
        self.traces = []
        return traces

    def pop_services(self):
        # dummy method

        # Setting service info has been deprecated, we want to make sure nothing ever gets written here
        assert self.services == {}
        s = self.services
        self.services = {}
        return s


class DummyTracer(Tracer):
    """
    DummyTracer is a tracer which uses the DummyWriter by default
    """
    def __init__(self):
        super(DummyTracer, self).__init__()
        self._update_writer()

    def _update_writer(self):
        # Track which writer the DummyWriter was created with, used
        # some tests
        if not isinstance(self.writer, DummyWriter):
            self.original_writer = self.writer
        # LogWriters don't have an api property, so we test that
        # exists before using it to assign hostname/port
        if hasattr(self.writer, 'api'):
            self.writer = DummyWriter(
                hostname=self.writer.api.hostname,
                port=self.writer.api.port,
                filters=self.writer._filters,
                priority_sampler=self.writer._priority_sampler,
            )
        else:
            self.writer = DummyWriter(
                hostname="",
                port=0,
                filters=self.writer._filters,
                priority_sampler=self.writer._priority_sampler,
            )

    def configure(self, *args, **kwargs):
        super(DummyTracer, self).configure(*args, **kwargs)
        # `.configure()` may reset the writer
        self._update_writer()