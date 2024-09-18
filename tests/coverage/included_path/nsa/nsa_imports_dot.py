from .normal_import_const import NSA_NORMAL
from .nsa.normal_import_const import NSA_NSA_NORMAL
from .nsb.normal_import_const import NSA_NSB_NORMAL


def nsa_imports_dot_normal():
    return NSA_NORMAL + NSA_NSA_NORMAL + NSA_NSB_NORMAL


def nsa_imports_dot_late():
    from .late_import_const import NSA_LATE
    from .nsa.late_import_const import NSA_NSA_LATE
    from .nsb.late_import_const import NSA_NSB_LATE

    return NSA_LATE + NSA_NSA_LATE + NSA_NSB_LATE
