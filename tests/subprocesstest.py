"""
subprocesstest enables unittest test cases and suites to be run in separate
python interpreter instances, in parallel.

A base class SubprocessTestCase is provided that, when extended, will run test
cases marked with @run_in_subprocess in a separate python interpreter.
"""
import unittest
import subprocess
import sys


SUBPROC_TEST_ATTR = '_subproc_test'


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

    def _run_test_in_subprocess(self, test, result):
        full_testcase_name = self._full_method_name(test)

        sp_test_cmd = ['python', '-m', 'unittest', full_testcase_name]
        sp = subprocess.Popen(
            sp_test_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = sp.communicate()

        if sp.returncode:
            try:
                cmdf = ' '.join(sp_test_cmd)
                raise Exception('Subprocess Test "{}" Failed'.format(cmdf))
            except Exception:
                exc_info = sys.exc_info()
            sys.stderr.write(stderr)
            result.addFailure(test, exc_info)
        else:
            result.addSuccess(test)

    def _in_subprocess(self, test):
        """Determines if the test is being run in a subprocess.

        This is done by checking the system arguments and seeing if the full
        module method name is contained in any of the arguments. This method
        assumes that the test case is being run in a subprocess if invoked with
        a test command specifying only this test case.

        For example the command:
        $ python -m unittest tests.contrib.gevent.test_patch.TestGeventPatch.test_patch_before_import

        will have _in_subprocess return True for the test_patch_before_import
        test case for gevent.

        :param test: the test case being run
        :return: whether the test is being run individually (with the assumption
                 that this is in a new subprocess)
        """
        full_testcase_name = self._full_method_name(test)
        for arg in sys.argv:
            if full_testcase_name in arg:
                return True
        return False

    def _is_subprocess_test(self, test):
        return hasattr(self, SUBPROC_TEST_ATTR) or hasattr(test, SUBPROC_TEST_ATTR)

    def run(self, result=None):
        test_method = getattr(self, self._testMethodName)

        if not self._is_subprocess_test(test_method):
            return super(SubprocessTestCase, self).run(result=result)

        if self._in_subprocess(test_method):
            return super(SubprocessTestCase, self).run(result=result)
        else:
            self._run_test_in_subprocess(test_method, result)
