from ..namespacepackage_b.normal_import_const import NSB_NORMAL


def nsa_imports_dotdot_nsb_normal():
    return NSB_NORMAL


def nsa_imports_dotdot_nsb_late():
    from ..namespacepackage_b.late_import_const import NSB_LATE

    return NSB_LATE
