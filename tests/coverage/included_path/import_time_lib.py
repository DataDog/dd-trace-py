"""Variables / functions in this file are used to test coverage of items at import time, rather than execution time"""

from tests.coverage.included_path.nested_import_time_lib import NESTED_CONSTANT


def compute_using_nested_constant():
    ret_val = "computed " + NESTED_CONSTANT
    return ret_val
