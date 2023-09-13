#!/usr/bin/env python3
#
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Dict
    from typing import Tuple

    from ._taint_tracking import Source

_IAST_TAINT_DICT = {}  # type: Dict[int, Tuple[Tuple[Source, int, int],...]]


def get_taint_dict():  # type: () -> Dict[int, Tuple[Tuple[Source, int, int],...]]
    return _IAST_TAINT_DICT


def clear_taint_mapping():  # type: () -> None
    global _IAST_TAINT_DICT
    _IAST_TAINT_DICT = {}
