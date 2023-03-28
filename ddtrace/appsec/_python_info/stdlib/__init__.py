#!/usr/bin/env python3

from sys import version_info

from .module_names import STDLIB_MODULE_NAMES


def _stdlib_for_python_version():  # type: () -> list
    return STDLIB_MODULE_NAMES[(version_info[0], version_info[1])]
