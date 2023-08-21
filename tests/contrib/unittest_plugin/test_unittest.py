import sys
import unittest

import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.unittest.constants import COMPONENT_VALUE
from ddtrace.contrib.unittest.constants import FRAMEWORK
from ddtrace.contrib.unittest.constants import KIND
from ddtrace.contrib.unittest.patch import _set_tracer
from ddtrace.contrib.unittest.patch import patch
from ddtrace.ext import SpanTypes
from ddtrace.ext import test
from ddtrace.internal.constants import COMPONENT
from tests.utils import TracerTestCase
from tests.utils import override_env


class UnittestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    with override_env(dict(DD_API_KEY="foobar.baz")):
        patch()

        def test_unittest_pass_single(self):
            """Test with a `unittest` test which should pass."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                def test_will_pass_first(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()
            assert len(spans) == 1

            assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[0].get_tag(SPAN_KIND) == KIND
            assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.NAME) == "test_will_pass_first"
            assert spans[0].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
            assert spans[0].get_tag(test.SUITE) == "UnittestExampleTestCase"
            assert spans[0].get_tag(test.TEST_STATUS) == test.Status.PASS.value

        def test_unittest_pass_multiple(self):
            """Tests with`unittest` tests which should pass."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                def test_will_pass_first(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                def test_will_pass_second(self):
                    self.assertTrue(3 == 3)
                    self.assertTrue(4 != 5)

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()
            assert len(spans) == 2
            for i in range(len(spans)):
                assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
                assert spans[i].get_tag(SPAN_KIND) == KIND
                assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
                assert spans[i].get_tag(test.TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
                assert spans[i].get_tag(test.SUITE) == "UnittestExampleTestCase"
                assert spans[i].get_tag(test.SKIP_REASON) is None

            assert spans[0].get_tag(test.NAME) == "test_will_pass_first"
            assert spans[0].get_tag(test.TEST_STATUS) == test.Status.PASS.value

            assert spans[1].get_tag(test.NAME) == "test_will_pass_second"
            assert spans[1].get_tag(test.TEST_STATUS) == test.Status.PASS.value

        @pytest.mark.skipif(
            sys.version_info[0] <= 3 and sys.version_info[1] <= 7, reason="Triggers a bug with skip reason being empty"
        )
        def test_unittest_skip_single_no_reason(self):
            """Tests with a `unittest` test which should be skipped."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                @unittest.skip
                def test_will_be_skipped(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()
            assert len(spans) == 1

            assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[0].get_tag(SPAN_KIND) == KIND
            assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.NAME) == "test_will_be_skipped"
            assert spans[0].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
            assert spans[0].get_tag(test.SUITE) == "UnittestExampleTestCase"
            assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
            assert spans[0].get_tag(test.SKIP_REASON) == ""

        def test_unittest_skip_single_reason(self):
            """Tests with a `unittest` test which should be skipped."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                @unittest.skip("demonstrating skipping with a reason")
                def test_will_be_skipped(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()
            assert len(spans) == 1

            assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[0].get_tag(SPAN_KIND) == KIND
            assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.NAME) == "test_will_be_skipped"
            assert spans[0].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
            assert spans[0].get_tag(test.SUITE) == "UnittestExampleTestCase"
            assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
            assert spans[0].get_tag(test.SKIP_REASON) == "demonstrating skipping with a reason"

        def test_unittest_skip_multiple_reason(self):
            """Test with `unittest` tests which should be skipped."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                @unittest.skip("demonstrating skipping with a reason")
                def test_will_be_skipped_first(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                @unittest.skip("demonstrating skipping with a different reason")
                def test_will_be_skipped_second(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()
            assert len(spans) == 2

            assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[0].get_tag(SPAN_KIND) == KIND
            assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.NAME) == "test_will_be_skipped_first"
            assert spans[0].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
            assert spans[0].get_tag(test.SUITE) == "UnittestExampleTestCase"
            assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
            assert spans[0].get_tag(test.SKIP_REASON) == "demonstrating skipping with a reason"

            assert spans[1].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[1].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[1].get_tag(SPAN_KIND) == KIND
            assert spans[1].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[1].get_tag(test.TYPE) == SpanTypes.TEST
            assert spans[1].get_tag(test.NAME) == "test_will_be_skipped_second"
            assert spans[1].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
            assert spans[1].get_tag(test.SUITE) == "UnittestExampleTestCase"
            assert spans[1].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
            assert spans[1].get_tag(test.SKIP_REASON) == "demonstrating skipping with a different reason"

        @pytest.mark.skipif(
            sys.version_info[0] <= 3 and sys.version_info[1] <= 7, reason="Triggers a bug with skip reason being empty"
        )
        def test_unittest_skip_combined(self):
            """Test with `unittest` tests which should be skipped."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                @unittest.skip
                def test_will_be_skipped(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                def test_wont_be_skipped(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()
            assert len(spans) == 2

            for i in range(len(spans)):
                assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
                assert spans[i].get_tag(SPAN_KIND) == KIND
                assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
                assert spans[i].get_tag(test.TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
                assert spans[i].get_tag(test.SUITE) == "UnittestExampleTestCase"

            assert spans[0].get_tag(test.NAME) == "test_will_be_skipped"
            assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
            assert spans[0].get_tag(test.SKIP_REASON) == ""

            assert spans[1].get_tag(test.NAME) == "test_wont_be_skipped"
            assert spans[1].get_tag(test.TEST_STATUS) == test.Status.PASS.value
            assert spans[1].get_tag(test.SKIP_REASON) is None

        def test_unittest_fail_single(self):
            """Test with `unittest` tests which should fail."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                def test_will_fail(self):
                    self.assertTrue(2 != 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()
            assert len(spans) == 1

            assert spans[0].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[0].get_tag(SPAN_KIND) == KIND
            assert spans[0].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[0].get_tag(test.TYPE) == SpanTypes.TEST
            assert spans[0].get_tag(test.NAME) == "test_will_fail"
            assert spans[0].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
            assert spans[0].get_tag(test.SUITE) == "UnittestExampleTestCase"
            assert spans[0].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
            assert spans[0].get_tag(test.SKIP_REASON) is None
            assert spans[0].get_tag(ERROR_MSG) == "False is not true"
            if sys.version_info[0] >= 3:
                assert spans[0].get_tag(ERROR_TYPE) == "builtins.AssertionError"
            else:
                assert spans[0].get_tag(ERROR_TYPE) == "exceptions.AssertionError"

        def test_unittest_fail_multiple(self):
            """Test with `unittest` tests which should fail."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                def test_will_fail_first(self):
                    self.assertTrue(2 != 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                def test_will_fail_second(self):
                    self.assertTrue(2 != 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()
            assert len(spans) == 2

            for i in range(len(spans)):
                assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
                assert spans[i].get_tag(SPAN_KIND) == KIND
                assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
                assert spans[i].get_tag(test.TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
                assert spans[i].get_tag(test.SUITE) == "UnittestExampleTestCase"
                assert spans[i].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
                assert spans[i].get_tag(test.SKIP_REASON) is None
                assert spans[i].get_tag(ERROR_MSG) == "False is not true"
                if sys.version_info[0] >= 3:
                    assert spans[i].get_tag(ERROR_TYPE) == "builtins.AssertionError"
                else:
                    assert spans[i].get_tag(ERROR_TYPE) == "exceptions.AssertionError"

            assert spans[0].get_tag(test.NAME) == "test_will_fail_first"

            assert spans[1].get_tag(test.NAME) == "test_will_fail_second"

        def test_unittest_combined(self):
            """Test with `unittest` tests which pass, get skipped and fail combined."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                def test_will_pass_first(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                def test_will_fail_first(self):
                    self.assertTrue(2 != 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                @unittest.skip("another skip reason")
                def test_will_be_skipped_with_a_reason(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()
            assert len(spans) == 3
            for i in range(len(spans)):
                assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
                assert spans[i].get_tag(SPAN_KIND) == KIND
                assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
                assert spans[i].get_tag(test.TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
                assert spans[i].get_tag(test.SUITE) == "UnittestExampleTestCase"

            assert spans[0].get_tag(test.NAME) == "test_will_be_skipped_with_a_reason"
            assert spans[0].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
            assert spans[0].get_tag(test.SKIP_REASON) == "another skip reason"

            assert spans[1].get_tag(test.NAME) == "test_will_fail_first"
            assert spans[1].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
            assert spans[1].get_tag(test.SKIP_REASON) is None
            assert spans[1].get_tag(ERROR_MSG) == "False is not true"
            if sys.version_info[0] >= 3:
                assert spans[1].get_tag(ERROR_TYPE) == "builtins.AssertionError"
            else:
                assert spans[1].get_tag(ERROR_TYPE) == "exceptions.AssertionError"

            assert spans[2].get_tag(test.NAME) == "test_will_pass_first"
            assert spans[2].get_tag(test.TEST_STATUS) == test.Status.PASS.value
            assert spans[2].get_tag(test.SKIP_REASON) is None

        def test_unittest_nested_test_cases(self):
            """Test with `unittest` test cases which pass, get skipped and fail combined."""
            _set_tracer(self.tracer)

            class UnittestExampleTestCase(unittest.TestCase):
                class SubTest1(unittest.TestCase):
                    def test_subtest1_will_pass_first(self):
                        self.assertTrue(2 == 2)
                        self.assertTrue("test string" == "test string")
                        self.assertFalse("not equal to" == "this")

                    def test_subtest1_will_fail_first(self):
                        self.assertTrue(2 != 2)
                        self.assertTrue("test string" == "test string")
                        self.assertFalse("not equal to" == "this")

                    @unittest.skip("another skip reason for subtest1")
                    def test_subtest1_will_be_skipped_with_a_reason(self):
                        self.assertTrue(2 == 2)
                        self.assertTrue("test string" == "test string")
                        self.assertFalse("not equal to" == "this")

                class SubTest2(unittest.TestCase):
                    def test_subtest2_will_pass(self):
                        self.assertTrue(2 == 2)
                        self.assertTrue("test string" == "test string")
                        self.assertFalse("not equal to" == "this")

                    @unittest.skip("another skip reason for subtest2")
                    def test_subtest2_will_be_skipped_with_a_reason(self):
                        self.assertTrue(2 == 2)
                        self.assertTrue("test string" == "test string")
                        self.assertFalse("not equal to" == "this")

            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase.SubTest1)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(UnittestExampleTestCase.SubTest2))
            unittest.TextTestRunner(verbosity=0).run(suite)

            spans = self.pop_spans()

            expected_result = [
                {
                    test.NAME: "test_subtest1_will_be_skipped_with_a_reason",
                    test.TEST_STATUS: test.Status.SKIP.value,
                    test.SKIP_REASON: "another skip reason for subtest1",
                    test.SUITE: "SubTest1",
                },
                {
                    test.NAME: "test_subtest1_will_fail_first",
                    test.TEST_STATUS: test.Status.FAIL.value,
                    test.SUITE: "SubTest1",
                    ERROR_MSG: "False is not true",
                    ERROR_TYPE: "builtins.AssertionError" if sys.version_info[0] >= 3 else "exceptions.AssertionError",
                },
                {
                    test.NAME: "test_subtest1_will_pass_first",
                    test.TEST_STATUS: test.Status.PASS.value,
                    test.SUITE: "SubTest1",
                },
                {
                    test.NAME: "test_subtest2_will_be_skipped_with_a_reason",
                    test.TEST_STATUS: test.Status.SKIP.value,
                    test.SKIP_REASON: "another skip reason for subtest2",
                    test.SUITE: "SubTest2",
                },
                {
                    test.NAME: "test_subtest2_will_pass",
                    test.TEST_STATUS: test.Status.PASS.value,
                    test.SUITE: "SubTest2",
                },
            ]

            assert len(spans) == 5
            for i in range(len(spans)):
                assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
                assert spans[i].get_tag(SPAN_KIND) == KIND
                assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
                assert spans[i].get_tag(test.TYPE) == SpanTypes.TEST
                assert spans[i].get_tag(test.MODULE) == "tests.contrib.unittest_plugin.test_unittest"
                assert spans[i].get_tag(test.NAME) == expected_result[i].get(test.NAME, None)
                assert spans[i].get_tag(test.TEST_STATUS) == expected_result[i].get(test.TEST_STATUS, None)
                assert spans[i].get_tag(test.SKIP_REASON) == expected_result[i].get(test.SKIP_REASON, None)
                assert spans[i].get_tag(test.SUITE) == expected_result[i].get(test.SUITE, None)
                assert spans[i].get_tag(ERROR_MSG) == expected_result[i].get(ERROR_MSG, None)
                assert spans[i].get_tag(ERROR_TYPE) == expected_result[i].get(ERROR_TYPE, None)
