"""
cleantest enables unittest test cases and suites to be run in separate python
interpreter instances, in parallel.
"""
import os
import pickle
import unittest
import subprocess
import sys


DEFAULT_NUM_PROCS = 8
SUBPROC_TEST_ATTR = '_subproc_test'
IN_SUBPROC_TEST_ENV = 'DD_IN_SUBPROC'


def run_in_subprocess(obj):
    """
    Marks a test case that is to be run in its own 'clean' interpreter instance.

    When applied to a TestCase class, each method will be run in a separate
    interpreter instance, in parallel.

    Usage on a class::

        @run_in_subprocess
        class PatchTests(object):
            # will be run in new interpreter
            def test_patch_before_import(self):
                patch()
                import module

            # will be run in new interpreter as well
            def test_patch_after_import(self):
                import module
                patch()


    Usage on a test method::

        class OtherTests(object):
            @run_in_subprocess
            def test_case(self):
                pass


    :param obj: method or class to run in a separate python interpreter.
    :return:
    """
    setattr(obj, SUBPROC_TEST_ATTR, True)
    return obj


class SubprocessTestCase(unittest.TestCase):
    def _full_method_name(self, test):
        modpath = test.__module__
        clsname = self.__class__.__name__
        testname = test.__name__
        testcase_name = '{}.{}.{}'.format(modpath, clsname, testname)
        return testcase_name

    @staticmethod
    def _merge_result(into_result, new_result):
        into_result.failures += new_result.failures
        into_result.errors += new_result.errors
        into_result.skipped += new_result.skipped
        into_result.expectedFailures += new_result.expectedFailures
        into_result.unexpectedSuccesses += new_result.unexpectedSuccesses
        into_result.testsRun += new_result.testsRun

    def _run_test_in_subprocess(self, test):
        full_testcase_name = self._full_method_name(test)

        env_var = '{}=True'.format(IN_SUBPROC_TEST_ENV)
        output = subprocess.check_output(
            [env_var, 'python', '-m', 'unittest', full_testcase_name],
            stderr=subprocess.STDOUT,  # cleantestrunner outputs to stderr
        )
        result = pickle.loads(output)
        return result

    @staticmethod
    def _in_subprocess():
        return bool(os.environ.get(IN_SUBPROC_TEST_ENV, 'False'))

    def _is_subprocess_test(self, test):
        return hasattr(self, SUBPROC_TEST_ATTR) or hasattr(test, SUBPROC_TEST_ATTR)

    def run(self, result=None):
        test_method = getattr(self, self._testMethodName)

        if not self._is_subprocess_test(test_method):
            return super(SubprocessTestCase, self).run(result=result)

        if self._in_subprocess():
            result = unittest.TestResult()
            super(SubprocessTestCase, self).run(result=result)
            result._original_stderr = None
            result._original_stdout = None
            # serialize and write the results to stderr
            sys.stderr.write(pickle.dumps(result))
            return result
        else:
            test_result = self._run_test_in_subprocess(test_method)
            self._merge_result(result, test_result)
            return result
