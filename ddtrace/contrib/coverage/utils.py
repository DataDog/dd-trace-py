import sys


def is_coverage_imported() -> bool:
    return "coverage" in sys.modules
