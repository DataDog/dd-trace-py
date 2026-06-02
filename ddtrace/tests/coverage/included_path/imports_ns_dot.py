from .normal_import_const import NORMAL
from .nsa.normal_import_const import NSA_NORMAL
from .nsa.nsb.normal_import_const import NSA_NSB_NORMAL
from .nsb.normal_import_const import NSB_NORMAL


def imports_ns_dot_normal():
    return NORMAL + NSA_NORMAL + NSA_NSB_NORMAL + NSB_NORMAL


def imports_ns_dot_late():
    from .late_import_const import LATE
    from .nsa.late_import_const import NSA_LATE
    from .nsa.nsa.late_import_const import NSA_NSA_LATE
    from .nsa.nsb.late_import_const import NSA_NSB_LATE
    from .nsb.late_import_const import NSB_LATE

    return LATE + NSA_LATE + NSA_NSA_LATE + NSA_NSB_LATE + NSB_LATE
