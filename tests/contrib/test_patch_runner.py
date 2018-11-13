from __future__ import print_function

import argparse
from collections import namedtuple
import multiprocessing.dummy
import unittest
import subprocess
import sys
import os


parser = argparse.ArgumentParser(description='Run patch tests.')
parser.add_argument('dir', metavar='directory', type=str,
                    help='directory to search for patch tests')
args = parser.parse_args()


def is_iterable(i):
    try:
        iter(i)
    except TypeError:
        return False
    else:
        return True


class FreshTestRunner(unittest.TextTestRunner):
    TestResult = namedtuple('TestResult', 'test returncode output')

    def __init__(self, modprefix='', *args, **kwargs):
        self.modprefix = modprefix
        super(FreshTestRunner, self).__init__(*args, **kwargs)

    def get_tests_from_suite(self, suite):
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

    def test_name(self, test):
        return '{}.{}'.format(
            unittest.util.strclass(test.__class__),
            test._testMethodName
        )

    def run_test(self, test):
        name = self.test_name(test)
        testcase_name = '{}.{}'.format(self.modprefix, name)
        try:
            out = subprocess.check_output(
                ['python', '-m', 'unittest', testcase_name],
                stderr=subprocess.STDOUT, # redirect stderr to stdout to collect and not output
            )
            return self.TestResult(test, 0, out.decode())
        except subprocess.CalledProcessError as err:
            return self.TestResult(test, err.returncode, err.output.decode())

    def run(self, suite):
        result = self._makeResult()
        unittest.signals.registerResult(result)
        tests = self.get_tests_from_suite(suite)

        pool = multiprocessing.dummy.Pool(8)
        test_results = pool.map(self.run_test, tests)

        # DEV: sort the failures to the bottom of output for convenience
        # ordering of the tests does not matter anyways since they are run in
        # parallel.
        test_results = sorted(test_results, key=lambda x: x.returncode)
        for test, status, output in test_results:
            # print to stderr since this is the convention for test runners
            print(output, file=sys.stderr)
            if status:
                # can't get sys.exc_info tuple from the failed test case
                result.addFailure(test, sys.exc_info())
            else:
                result.addSuccess(test)

        return result


if __name__ == '__main__':
    cwd = os.getcwd()
    sys.path.pop(0)
    sys.path.insert(0, cwd)
    test_dir = os.path.join(cwd, args.dir)
    modprefix = args.dir.replace(os.path.sep, '.')
    loader = unittest.TestLoader()
    suite = loader.discover(test_dir, pattern='test_patch.py')
    patch_tests_result = FreshTestRunner(modprefix).run(suite)
    sys.exit(not patch_tests_result.wasSuccessful())
