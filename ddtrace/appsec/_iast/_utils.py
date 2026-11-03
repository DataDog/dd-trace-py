from typing import Hashable
from typing import Sequence

from ddtrace.internal.settings.asm import config as asm_config


def _get_source_index(sources: Sequence[Hashable], source: Hashable) -> int:
    i = 0
    for source_ in sources:
        if hash(source_) == hash(source):
            return i
        i += 1
    return -1


def _is_iast_debug_enabled() -> bool:
    enabled: bool = asm_config._iast_debug
    return enabled


def _is_iast_propagation_debug_enabled() -> bool:
    enabled: bool = asm_config._iast_propagation_debug
    return enabled
