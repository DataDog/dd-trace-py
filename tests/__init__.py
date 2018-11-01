import unittest

import wrapt

from .utils.tracer import DummyTracer
from .utils.span import TestSpanContainer


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


class BaseTracerTestCase(TestSpanContainer, BaseTestCase):
    """
    BaseTracerTestCase is a base test case for when you need access to a dummy tracer and span assertions
    """
    def setUp(self):
        self.tracer = DummyTracer()

        super(BaseTracerTestCase, self).setUp()

    def tearDown(self):
        super(BaseTracerTestCase, self).tearDown()

        self.reset()
        delattr(self, 'tracer')

    @property
    def spans(self):
        return self._ensure_test_spans(self.tracer.writer.spans)

    def reset(self):
        self.tracer.writer.pop()

    def assert_structure(self, root, children):
        root_span = self.get_root_span()
        root_span.assert_structure(root, children)
