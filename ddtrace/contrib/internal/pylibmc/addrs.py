from collections.abc import Callable
from collections.abc import Iterable
from typing import Optional


translate_server_specs: Optional[Callable[[Iterable[str]], list[tuple[int, str, int, int]]]] = None

try:
    # NOTE: we rely on an undocumented method to parse addresses,
    # so be a bit defensive and don't assume it exists.
    from pylibmc.client import translate_server_specs as _translate_server_specs

    translate_server_specs = _translate_server_specs
except ImportError:
    pass


def parse_addresses(addrs: Iterable[str]) -> list[tuple[int, str, int, int]]:
    if not translate_server_specs:
        return []
    return translate_server_specs(addrs)
