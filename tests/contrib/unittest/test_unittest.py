import unittest

import pytest

from ddtrace.contrib.unittest.patch import _set_tracer
from tests.utils import TracerTestCase


class UnittestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

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

    def test_unittest_skip_multiple_reason(self):
        """Test with `unittest` tests which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @unittest.skip("demonstrating skipping with a reason")
            def test_will_be_skipped_first(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

            @unittest.skip("demonstrating skipping with a reason")
            def test_will_be_skipped_second(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 2

    def test_unittest_skip_combined(self):
        """Test with `unittest` tests which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @pytest.skip()
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

            @pytest.skip()
            def test_will_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue('test string' == 'test string')
                self.assertFalse('not equal to' == 'this')

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 3
