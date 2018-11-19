"""
Runner required for cleantest to serialize and output the results of a single
test case.
"""
import pickle
import unittest
import sys


class TestRunner(object):
    def run(self, test):
        result = unittest.TestResult()
        # can't serialize file objects
        result._original_stderr = None
        result._original_stdout = None
        test(result)
        sys.stderr.write(pickle.dumps(result))
        return result


if __name__ == '__main__':
    unittest.TestProgram(module=None, testRunner=TestRunner)
