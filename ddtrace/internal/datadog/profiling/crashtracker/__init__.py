# See ../ddup/__init__.py for some discussion on the is_available attribute.
# The configuration for this feature is handled in ddtrace/settings/crashtracker.py.
is_available = False
failure_msg = ""


try:
    from ._crashtracker import *  # noqa: F403, F401

    is_available = True

except Exception as e:
    failure_msg = str(e)
