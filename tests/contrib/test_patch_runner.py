from __future__ import print_function

import argparse
import multiprocessing.dummy
import unittest
import subprocess
import sys
import os


parser = argparse.ArgumentParser(description='Run patch tests.')
parser.add_argument('dir', metavar='directory', type=str,
                    help='directory to search for patch tests')
args = parser.parse_args()


def _get_tests_from_suite(suite):
    tests = []
    suites_to_check = [suite]
    while suites_to_check:
        suite = suites_to_check.pop()
        for s in suite:
            if _iterable(s):
                suites_to_check.append(s)
            else:
                tests.append(s)
    return tests


def _iterable(i):
    try:
        iter(i)
    except TypeError:
        return False
    else:
        return True


def test_name(test):
    return '{}.{}'.format(unittest.util.strclass(test.__class__), test._testMethodName)


def run_test(test):
    name = test_name(test)
    modprefix = args.dir.replace(os.path.sep, '.')
    testcase = '{}.{}'.format(modprefix, name)
    try:
        out = subprocess.check_output(
            ['python', '-m', 'unittest', '-v', testcase],
            stderr=subprocess.STDOUT, # redirect stderr to stdout to collect and not output
        )
        return (0, out)
    except subprocess.CalledProcessError as err:
        return (err.returncode, err.output)



if __name__ == '__main__':
    cwd = os.getcwd()
    sys.path.pop(0)
    sys.path.insert(0, cwd)
    test_dir = os.path.join(cwd, args.dir)
    loader = unittest.TestLoader()
    suite = loader.discover(test_dir, pattern='test_patch.py')
    tests = _get_tests_from_suite(suite)

    pool = multiprocessing.dummy.Pool(8)
    test_results = pool.map(run_test, tests)
    for status, output in test_results:
        # print to stderr since this is the convention for test runners
        print(output, file=sys.stderr)

    if sum(map(lambda x: x[0], test_results)):
        sys.exit(1)
