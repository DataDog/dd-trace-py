from typing import List

from ddtrace.settings.asm import config as asm_config


def _get_source_index(sources: List, source) -> int:
    i = 0
    for source_ in sources:
        if hash(source_) == hash(source):
            return i
        i += 1
    return -1


def _is_iast_debug_enabled():
    return asm_config._iast_debug


def _is_iast_propagation_debug_enabled():
    return asm_config._iast_propagation_debug
