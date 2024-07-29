from .normal_import_const import NORMAL
from .rpa.normal_import_const import RPA_NORMAL
from .rpa.rpb.normal_import_const import RPA_RPB_NORMAL
from .rpb.normal_import_const import RPB_NORMAL


def imports_rp_dot_normal():
    return NORMAL + RPA_NORMAL + RPA_RPB_NORMAL + RPB_NORMAL


def imports_rp_dot_late():
    from .late_import_const import LATE
    from .rpa.late_import_const import RPA_LATE
    from .rpa.rpa.late_import_const import RPA_RPA_LATE
    from .rpa.rpb.late_import_const import RPA_RPB_LATE
    from .rpb.late_import_const import RPB_LATE

    return LATE + RPA_LATE + RPA_RPA_LATE + RPA_RPB_LATE + RPB_LATE
