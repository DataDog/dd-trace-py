import sys


def is_coverage_imported():
    return "coverage" in sys.modules
