from tests.coverage.included_path.import_time_lib import CONSTANT, UNUSED_CONSTANT
from tests.coverage.included_path.import_time_lib import COMPUTED_CONSTANT
from tests.coverage.included_path.import_time_lib import OVERWRITTEN_CONSTANT
from tests.coverage.included_path.import_time_lib import OVERWRITTEN_IN_FUNCTION_CONSTANT
from tests.coverage.included_path.import_time_lib import ran_at_import_time
from tests.coverage.included_path.import_time_lib import ran_and_assigned_at_import_time

ran_at_import_time()

RAN_AT_IMPORT_TIME = ran_and_assigned_at_import_time()

OVERWRITTEN_CONSTANT = "overwritten in module"

OVERWRITTEN_BY_IMPORT_CONSTANT = "will be overwritten by import"

from tests.coverage.included_path.import_time_lib import OVERWRITTEN_BY_IMPORT_CONSTANT

OVERWRITTEN_BY_IMPORT_AS_CONSTANT = "will be overwritten by import as"
from tests.coverage.included_path.import_time_lib import OVERWRITING_BY_IMPORT_AS_CONSTANT as OVERWRITTEN_BY_IMPORT_AS_CONSTANT

GLOBAL_CONSTANT = "global constant"

LOCAL_GLOBAL_CONSTANT = GLOBAL_CONSTANT

def called_in_session(a, b):
    return (a, b)


def never_called_in_session():  # Should be covered due to import
    # Should not be covered because it is not
    pass

def uses_constant_in_session():
    str1 = CONSTANT
    return str1

def uses_computed_constant_in_session():
    str1 = COMPUTED_CONSTANT
    return str1

def uses_ran_at_import_time_in_session():
    str1 = RAN_AT_IMPORT_TIME
    return str1

def uses_overwritten_at_import_time_in_session():
    str1 = OVERWRITTEN_CONSTANT
    return str1

def uses_overwritten_in_function_at_import_time_in_session():
    OVERWRITTEN_IN_FUNCTION_CONSTANT = "overwritten in function"
    str1 = OVERWRITTEN_IN_FUNCTION_CONSTANT
    return str1

def uses_global_constant_in_function_in_session():
    global GLOBAL_CONSTANT
    GLOBAL_CONSTANT = "overwritten global constant"
    return GLOBAL_CONSTANT

def does_not_use_global_constant_in_function_in_session():
    GLOBAL_CONSTANT = "overwritten global constant"
    return GLOBAL_CONSTANT
