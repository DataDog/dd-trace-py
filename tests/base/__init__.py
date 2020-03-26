import contextlib
import sys

import ddtrace

from ..subprocesstest import SubprocessTestCase
from ..utils import override_env, assert_is_not_measured, assert_is_measured
from ..utils.tracer import DummyTracer
from ..utils.span import TestSpanContainer, TestSpan, NO_CHILDREN


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


# TODO[tbutt]: Remove this once all tests are properly using BaseTracerTestCase
override_config = BaseTestCase.override_config


class BaseTracerTestCase(TestSpanContainer, BaseTestCase):
    """
    BaseTracerTestCase is a base test case for when you need access to a dummy tracer and span assertions
    """
    def setUp(self):
        """Before each test case, setup a dummy tracer to use"""
        self.tracer = DummyTracer()

        super(BaseTracerTestCase, self).setUp()

    def tearDown(self):
        """After each test case, reset and remove the dummy tracer"""
        super(BaseTracerTestCase, self).tearDown()

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
