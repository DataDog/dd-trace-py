from .namespacepackage_a.normal_import_const import NSA_NSA_NORMAL
from .namespacepackage_b.normal_import_const import NSA_NSB_NORMAL
from .normal_import_const import NSA_NORMAL


def nsa_imports_dot_normal():
    return NSA_NORMAL + NSA_NSA_NORMAL + NSA_NSB_NORMAL


def nsa_imports_dot_late():
    from .late_import_const import NSA_LATE
    from .namespacepackage_a.late_import_const import NSA_NSA_LATE
    from .namespacepackage_b.late_import_const import NSA_NSB_LATE

    return NSA_LATE + NSA_NSA_LATE + NSA_NSB_LATE
