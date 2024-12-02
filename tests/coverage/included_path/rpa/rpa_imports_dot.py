from .normal_import_const import RPA_NORMAL
from .rpa.normal_import_const import RPA_RPA_NORMAL
from .rpb.normal_import_const import RPA_RPB_NORMAL


def rpa_imports_dot_normal():
    return RPA_NORMAL + RPA_RPA_NORMAL + RPA_RPB_NORMAL


def rpa_imports_dot_late():
    from .late_import_const import RPA_LATE
    from .rpa.late_import_const import RPA_RPA_LATE
    from .rpb.late_import_const import RPA_RPB_LATE

    return RPA_LATE + RPA_RPA_LATE + RPA_RPB_LATE
