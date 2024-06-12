from import_time_lib import CONSTANT
from import_time_lib import COMPUTED_CONSTANT
from import_time_lib import ran_at_import_time

RAN_AT_IMPORT_TIME = ran_at_import_time()

def called_in_context(a, b):
    return (a, b)

def call_in_context_nested(a, b):
    return (a, b)

def never_called_in_context():  # Should be covered due to import
    # Should not be covered because it is not
    pass

def uses_constant_in_context():
    str1 = CONSTANT
    return str1

def uses_computed_constant_in_context():
    str1 = COMPUTED_CONSTANT
    return str1

def uses_ran_at_import_time_in_context():
    str1 = RAN_AT_IMPORT_TIME
    return str1