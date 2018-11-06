import contextlib
import unittest

import wrapt

from ddtrace import config

from .utils.tracer import DummyTracer
from .utils.span import TestSpanContainer, TestSpan


class BaseTestCase(unittest.TestCase):
    """
    BaseTestCase extends ``unittest.TestCase`` to provide some useful helpers/assertions


    Example::

        from tests import BaseTestCase


        class MyTestCase(BaseTestCase):
            def test_case(self):
                self.assert_is_wrapped(obj)
    """
    def assert_is_wrapped(self, obj):
        """
        Assert that the provided ``obj`` is a ``wrapt.ObjectProxy`` instance

        :param obj: Object to assert
        :type obj: object
        :raises: AssertionError
        """
        self.assertTrue(isinstance(obj, wrapt.ObjectProxy))

    @contextlib.contextmanager
    def override_config(self, integration, values):
        """
        Temporarily override an integration configuration value
        >>> with self.override_config('flask', dict(service_name='test-service')):
            # Your test
        """
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

    def assert_structure(self, root, children):
        """Helper to call TestSpanNode.assert_structure on the current root span"""
        root_span = self.get_root_span()
        root_span.assert_structure(root, children)
