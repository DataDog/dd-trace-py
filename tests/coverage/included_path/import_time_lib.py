"""Variables / functions in this file are used to test coverage of items at import time, rather than execution time"""

CONSTANT = "some constant"
UNUSED_CONSTANT = "unused constant"
OVERWRITTEN_CONSTANT = "overwritten constant"  # This constant will be imported but the importer will overwrite it
OVERWRITTEN_IN_FUNCTION_CONSTANT = "overwritten in function constant"  # same as above, but overwritten in function
OVERWRITING_BY_IMPORT_AS_CONSTANT = "overwriting by import as"  # This constant will be imported in a way that overwrites the original
OVERWRITTEN_BY_IMPORT_CONSTANT = "overwritten by import"  # This constant will be imported in a way that overwrites

def compute_sort_of_constant(n: int = 1):
    ret_val = n * CONSTANT
    return ret_val

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
