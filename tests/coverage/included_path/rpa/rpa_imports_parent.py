"""This imports the parent package's constants"""
from ..normal_import_const import NORMAL


def rpa_imports_parent_normal():
    return NORMAL


def rpa_imports_parent_late():
    from ..late_import_const import LATE

    return LATE
