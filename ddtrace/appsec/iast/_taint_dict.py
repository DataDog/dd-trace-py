#!/usr/bin/env python3
#
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Dict
    from typing import Tuple

    from ddtrace.appsec.iast._input_info import Input_info

_IAST_TAINT_DICT = {}  # type: Dict[int, Tuple[Tuple[Input_info, int, int],...]]


def get_taint_dict():  # type: () -> Dict[int, Tuple[Tuple[Input_info, int, int],...]]
    return _IAST_TAINT_DICT


def clear_taint_mapping():  # type: () -> None
    global _IAST_TAINT_DICT
    _IAST_TAINT_DICT = {}
