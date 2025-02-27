#!/usr/bin/env python3

from sys import version_info


if version_info < (3, 9, 0):
    from .module_names_py38 import STDLIB_MODULE_NAMES
elif version_info < (3, 10, 0):
    from .module_names_py39 import STDLIB_MODULE_NAMES
elif version_info < (3, 11, 0):
    from .module_names_py310 import STDLIB_MODULE_NAMES
elif version_info < (3, 12, 0):
    from .module_names_py311 import STDLIB_MODULE_NAMES
else:
    from .module_names_py312 import STDLIB_MODULE_NAMES


def _stdlib_for_python_version():  # type: () -> set[str]
    return STDLIB_MODULE_NAMES
