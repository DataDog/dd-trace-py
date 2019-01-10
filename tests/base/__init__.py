import contextlib
import os
import unittest

from ddtrace import config

from ..utils.tracer import DummyTracer
from ..utils.span import TestSpanContainer, TestSpan, NO_CHILDREN


class BaseTestCase(unittest.TestCase):
    """
    BaseTestCase extends ``unittest.TestCase`` to provide some useful helpers/assertions


    Example::

        from tests import BaseTestCase


        class MyTestCase(BaseTestCase):
            def test_case(self):
                with self.override_config('flask', dict(distributed_tracing_enabled=True):
                    pass
    """

    @contextlib.contextmanager
    def override_env(self, env):
        """
        Temporarily override ``os.environ`` with provided values
        >>> with self.override_env(dict(DATADOG_TRACE_DEBUG=True)):
            # Your test
        """
        original = dict(
            (key, os.environ.get(key))
            for key in env.keys()
        )

        os.environ.update(env)
        try:
            yield
        finally:
            os.environ.update(original)

    @contextlib.contextmanager
    def override_config(self, integration, values):
        """
        Temporarily override an integration configuration value
        >>> with self.override_config('flask', dict(service_name='test-service')):
            # Your test
        """

        # IntegrationConfig
        options = getattr(config, integration)

        original = dict(
            (key, options.get(key))
            for key in values.keys()
        )

        options.update(values)
        try:
            yield
        finally:
            options.update(original)


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
