import unittest

import pytest

from ddtrace.constants import ERROR_MSG, ERROR_TYPE, SPAN_KIND
from ddtrace.contrib.unittest.constants import COMPONENT_VALUE, FRAMEWORK, KIND
from ddtrace.contrib.unittest.patch import _set_tracer, patch
from ddtrace.ext import test, SpanTypes
from ddtrace.internal.constants import COMPONENT
from tests.utils import TracerTestCase


class UnittestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    patch()

    def test_unittest_pass_single(self):
        """Test with a `unittest` test which should pass."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_pass_first(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 1

        assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[0].get_tag(SPAN_KIND) == KIND
        assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.NAME) == 'test_will_pass_first'
        assert spans[0].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[0].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[0].get_tag(test.TEST_STATUS) == test.Status.PASS.value

    def test_unittest_pass_multiple(self):
        """Tests with`unittest` tests which should pass."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_pass_first(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

            def test_will_pass_second(self):
                self.assertTrue(3 == 3)
                self.assertTrue(4 != 5)

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 2

        assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[0].get_tag(SPAN_KIND) == KIND
        assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.NAME) == 'test_will_pass_first'
        assert spans[0].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[0].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[0].get_tag(test.TEST_STATUS) == test.Status.PASS.value
        assert spans[0].get_tag(test.SKIP_REASON) is None

        assert spans[1].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[1].get_tag(SPAN_KIND) == KIND
        assert spans[1].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[1].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.NAME) == 'test_will_pass_second'
        assert spans[1].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[1].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[1].get_tag(test.TEST_STATUS) == test.Status.PASS.value
        assert spans[1].get_tag(test.SKIP_REASON) is None

    def test_unittest_skip_single(self):
        """Tests with a `unittest` test which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @unittest.skip
            def test_will_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 1

        assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[0].get_tag(SPAN_KIND) == KIND
        assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.NAME) == 'test_will_be_skipped'
        assert spans[0].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[0].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
        assert spans[0].get_tag(test.SKIP_REASON) == ''

    def test_unittest_skip_single_reason(self):
        """Tests with a `unittest` test which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @unittest.skip("demonstrating skipping with a reason")
            def test_will_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 1

        assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[0].get_tag(SPAN_KIND) == KIND
        assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.NAME) == 'test_will_be_skipped'
        assert spans[0].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[0].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
        assert spans[0].get_tag(test.SKIP_REASON) == 'demonstrating skipping with a reason'

    def test_unittest_skip_multiple_reason(self):
        """Test with `unittest` tests which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @unittest.skip("demonstrating skipping with a reason")
            def test_will_be_skipped_first(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

            @unittest.skip("demonstrating skipping with a different reason")
            def test_will_be_skipped_second(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 2

        assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[0].get_tag(SPAN_KIND) == KIND
        assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.NAME) == 'test_will_be_skipped_first'
        assert spans[0].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[0].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
        assert spans[0].get_tag(test.SKIP_REASON) == 'demonstrating skipping with a reason'

        assert spans[1].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[1].get_tag(SPAN_KIND) == KIND
        assert spans[1].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[1].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.NAME) == 'test_will_be_skipped_second'
        assert spans[1].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[1].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[1].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
        assert spans[1].get_tag(test.SKIP_REASON) == 'demonstrating skipping with a different reason'

    def test_unittest_skip_combined(self):
        """Test with `unittest` tests which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @unittest.skip
            def test_will_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

            def test_wont_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 2

        assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[0].get_tag(SPAN_KIND) == KIND
        assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.NAME) == 'test_will_be_skipped'
        assert spans[0].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[0].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
        assert spans[0].get_tag(test.SKIP_REASON) == ''

        assert spans[1].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[1].get_tag(SPAN_KIND) == KIND
        assert spans[1].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[1].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.NAME) == 'test_wont_be_skipped'
        assert spans[1].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[1].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[1].get_tag(test.TEST_STATUS) == test.Status.PASS.value
        assert spans[1].get_tag(test.SKIP_REASON) is None

    def test_unittest_fail_single(self):
        """Test with `unittest` tests which should fail."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_fail(self):
                self.assertTrue(2 != 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 1

        assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[0].get_tag(SPAN_KIND) == KIND
        assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.NAME) == 'test_will_fail'
        assert spans[0].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[0].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[0].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
        assert spans[0].get_tag(test.SKIP_REASON) is None
        assert spans[0].get_tag(ERROR_MSG) == 'False is not true'
        assert spans[0].get_tag(ERROR_TYPE) == 'builtins.AssertionError'

    def test_unittest_fail_multiple(self):
        """Test with `unittest` tests which should fail."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_fail_first(self):
                self.assertTrue(2 != 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

            def test_will_fail_second(self):
                self.assertTrue(2 != 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 2

        assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[0].get_tag(SPAN_KIND) == KIND
        assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.NAME) == 'test_will_fail_first'
        assert spans[0].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[0].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[0].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
        assert spans[0].get_tag(test.SKIP_REASON) is None
        assert spans[0].get_tag(ERROR_MSG) == 'False is not true'
        assert spans[0].get_tag(ERROR_TYPE) == 'builtins.AssertionError'

        assert spans[1].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[1].get_tag(SPAN_KIND) == KIND
        assert spans[1].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[1].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.NAME) == 'test_will_fail_second'
        assert spans[1].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[1].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[1].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
        assert spans[1].get_tag(test.SKIP_REASON) is None
        assert spans[1].get_tag(ERROR_MSG) == 'False is not true'
        assert spans[1].get_tag(ERROR_TYPE) == 'builtins.AssertionError'

    def test_unittest_combined(self):
        """Test with `unittest` tests which pass, get skipped and fail combined."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_pass_first(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

            def test_will_fail_first(self):
                self.assertTrue(2 != 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

            @unittest.skip
            def test_will_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

            @unittest.skip("another skip reason")
            def test_will_be_skipped_with_a_reason(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 4

        assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[0].get_tag(SPAN_KIND) == KIND
        assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[0].get_tag(test.NAME) == 'test_will_be_skipped'
        assert spans[0].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[0].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
        assert spans[0].get_tag(test.SKIP_REASON) == ''

        assert spans[1].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[1].get_tag(SPAN_KIND) == KIND
        assert spans[1].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[1].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[1].get_tag(test.NAME) == 'test_will_be_skipped_with_a_reason'
        assert spans[1].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[1].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[1].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
        assert spans[1].get_tag(test.SKIP_REASON) == 'another skip reason'

        assert spans[2].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[2].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[2].get_tag(SPAN_KIND) == KIND
        assert spans[2].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[2].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[2].get_tag(test.NAME) == 'test_will_fail_first'
        assert spans[2].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[2].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[2].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
        assert spans[2].get_tag(test.SKIP_REASON) is None
        assert spans[2].get_tag(ERROR_MSG) == 'False is not true'
        assert spans[2].get_tag(ERROR_TYPE) == 'builtins.AssertionError'

        assert spans[3].get_tag(test.TEST_TYPE) == SpanTypes.TEST
        assert spans[3].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
        assert spans[3].get_tag(SPAN_KIND) == KIND
        assert spans[3].get_tag(COMPONENT) == COMPONENT_VALUE
        assert spans[3].get_tag(test.TYPE) == SpanTypes.TEST
        assert spans[3].get_tag(test.NAME) == 'test_will_pass_first'
        assert spans[3].get_tag(test.MODULE) == 'tests.contrib.unittest_plugin.test_unittest'
        assert spans[3].get_tag(test.SUITE) == 'UnittestExampleTestCase'
        assert spans[3].get_tag(test.TEST_STATUS) == test.Status.PASS.value
        assert spans[3].get_tag(test.SKIP_REASON) is None
