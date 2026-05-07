# In case when _ddup is not available, we set is_available attribute to False,
# and failure_msg to the error message. This module is initially imported in
# ddtrace/settings/profiling.py to determine if profiling can be run. If it
# fails, we turn off profiling in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    from ._ddup import *  # noqa: F403, F401

    is_available = True

except Exception as e:
    failure_msg = str(e)
