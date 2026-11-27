from typing import Hashable
from typing import Sequence

from ddtrace.internal.settings.asm import config as asm_config


def _get_source_index(sources: Sequence[Hashable], source: Hashable) -> int:
    for i, source_ in enumerate(sources):
        if source_ is source or source_ == source:
            return i
    source_hash = hash(source)
    source_value = getattr(source, "value", None)
    if source_value is not None:
        for i, source_ in enumerate(sources):
            if hash(source_) == source_hash and getattr(source_, "value", None) == source_value:
                return i
    for i, source_ in enumerate(sources):
        if hash(source_) == source_hash:
            return i
    return -1


def _is_iast_debug_enabled() -> bool:
    enabled: bool = asm_config._iast_debug
    return enabled


def _is_iast_propagation_debug_enabled() -> bool:
    enabled: bool = asm_config._iast_propagation_debug
    return enabled
