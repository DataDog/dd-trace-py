# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    from ._stack import *  # noqa: F403, F401  # type: ignore[assignment]

    is_available = True

except Exception as e:
    failure_msg = str(e)
