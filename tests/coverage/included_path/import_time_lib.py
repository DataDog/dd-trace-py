"""Variables / functions in this file are used to test coverage of items at import time, rather than execution time"""

CONSTANT = "some constant"

def compute_sort_of_constant(n: int = 1):
    return n * CONSTANT

COMPUTED_CONSTANT = compute_sort_of_constant()

def ran_at_import_time():
    str1 = "ran"
    str2 = "at import time"
    return _inner_ran_at_import_time(str1, str2)

def _inner_ran_at_import_time(str1, str2):
    first = str1 + str2
    second = str2 + str1
    return str1, str2, first, second


def ran_and_assigned_at_import_time():
    str1 = "ran"
    str2 = "assigned"
    return _inner_ran_and_assigned_at_import_time(str1, str2)

def _inner_ran_and_assigned_at_import_time(str1, str2):
    first = str1 + str2
    second = str2 + str1
    return str1, str2, first, second
