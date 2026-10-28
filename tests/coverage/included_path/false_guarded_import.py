RUNTIME_FALSE = bool(0)

if RUNTIME_FALSE:
    from tests.coverage.included_path import imported_in_function_lib  # noqa:F401

from tests.coverage.included_path import import_time_lib  # noqa:E402


def called_after_import():
    return import_time_lib
