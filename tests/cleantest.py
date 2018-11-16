from collections import namedtuple
import multiprocessing.dummy
import unittest
import subprocess


def clean(obj):
    setattr(obj, '_test_clean', True)
    return obj


def is_iterable(i):
    try:
        iter(i)
    except TypeError:
        return False
    else:
        return True


def is_run_clean(test):
    try:
        if hasattr(test, '_test_clean'):
            return True
        if hasattr(test.testCase, '_test_clean'):
            return True
    except Exception:
        return False


class CleanTestSuite(unittest.TestSuite):
    TestResult = namedtuple('TestResult', 'test returncode output')

    def __init__(self, modprefix, *args, **kwargs):
        self.modprefix = modprefix
        super(CleanTestSuite, self).__init__(*args, **kwargs)

    @staticmethod
    def get_tests_from_suite(suite):
        tests = []
        suites_to_check = [suite]
        while suites_to_check:
            suite = suites_to_check.pop()
            for s in suite:
                if is_iterable(s):
                    suites_to_check.append(s)
                else:
                    tests.append(s)
        return tests

    @staticmethod
    def test_name(test):
        return '{}.{}'.format(
            unittest.util.strclass(test.__class__),
            test._testMethodName
        )

    def full_test_mod_name(self, test):
        name = self.test_name(test)
        testcase_name = '{}.{}'.format(self.modprefix, name)
        return testcase_name

    def run_test_in_subprocess(self, test):
        testcase_name = self.full_test_mod_name(test)
        try:
            out = subprocess.check_output(
                ['python', '-m', 'unittest', testcase_name],
                stderr=subprocess.STDOUT,  # redirect stderr to stdout to collect and not output
            )
            return self.TestResult(test, 0, out.decode())
        except subprocess.CalledProcessError as err:
            return self.TestResult(test, err.returncode, err.output.decode())

    @staticmethod
    def format_fail_output(output):
        # remove the leading 'F'
        output = output[1:]
        # remove the leading and trailing lines
        output = '\n'.join(output.split('\n')[4:-4])
        return output

    def run(self, result, debug=False):
        tests = self.get_tests_from_suite(self._tests)
        pool = multiprocessing.dummy.Pool(8)
        test_results = pool.map(self.run_test_in_subprocess, tests)

        for test, status, output in test_results:
            test_name = self.full_test_mod_name(test)
            result.testsRun += 1
            if status:
                # can't get sys.exc_info tuple from the failed test case
                result.addFailure(test, ('clean test {}'.format(test_name), self.format_fail_output(output), None))
            else:
                result.addSuccess(test)

        return result


def _close_prefix_clean_test_suite(modprefix):
    def get_clean_test_suite(*args, **kwargs):
        return CleanTestSuite(modprefix, *args, **kwargs)
    return get_clean_test_suite


class CleanTestLoader(unittest.TestLoader):
    def __init__(self, modprefix, *args, **kwargs):
        self.suiteClass = _close_prefix_clean_test_suite(modprefix)
        super(CleanTestLoader, self).__init__(*args, **kwargs)
