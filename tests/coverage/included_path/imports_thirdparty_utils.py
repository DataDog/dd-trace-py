# This module imports 'path' from the os package (simulating a third-party import)
# The resolution logic should NOT fall back to match a local 'path' module

from os import path as os_path


THIRDPARTY_IMPORT_RESULT = os_path.exists(".")


def uses_thirdparty_import():
    return THIRDPARTY_IMPORT_RESULT
