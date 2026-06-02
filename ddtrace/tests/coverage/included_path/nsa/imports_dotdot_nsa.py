from ..namespacepackage_a.normal_import_const import NSA_NORMAL


def nsa_imports_dotdot_nsb_normal():
    return NSA_NORMAL


def nsa_imports_dotdot_nsb_late():
    from ..namespacepackage_a.late_import_const import NSA_LATE

    return NSA_LATE
