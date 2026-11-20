from typing import Any
from typing import Optional
from typing import Protocol
from typing import Union

from _ddtrace_internal.utils import ArgumentError as ArgumentError  # noqa: F401 -- moved to ddtrace-internal
from _ddtrace_internal.utils import get_argument_value as get_argument_value  # noqa: F401
from _ddtrace_internal.utils import set_argument_value as set_argument_value  # noqa: F401


def _get_metas_to_propagate(context: Any) -> list[tuple[str, str]]:
    if context is None:
        return []
    # Snapshot items to avoid "dictionary changed size during iteration" when
    # context._meta is mutated by another thread (e.g. tracer.sample()). See #16523.
    items = list(context._meta.items())
    return [(k, v) for k, v in items if isinstance(k, str) and k.startswith("_dd.p.")]


class Block_config(Protocol):
    block_id: str
    grpc_status_code: int
    status_code: int
    type: str
    location: str
    content_type: str

    def get(self, key: str, default: Any = None) -> Union[str, int]: ...

    def __getitem__(self, key: str) -> Optional[Union[str, int]]: ...

    def __contains__(self, key: str) -> bool: ...


def get_blocked() -> Optional[Block_config]:
    # local import to avoid circular dependency
    from ddtrace.internal import core

    res = core.dispatch_with_results("asm.get_blocked")  # ast-grep-ignore: core-dispatch-with-results
    if res and res.block_config:
        return res.block_config.value
    return None


def set_blocked(block_settings: Optional[dict[str, Any]] = None) -> None:
    # local imports to avoid circular dependency
    from ddtrace.internal import core
    from ddtrace.internal.constants import STATUS_403_TYPE_AUTO

    core.dispatch("asm.set_blocked", (block_settings or STATUS_403_TYPE_AUTO,))


def _human_size(nbytes: float) -> str:
    """Return a human-readable size."""
    i = 0
    suffixes = ["B", "KB", "MB", "GB", "TB"]
    while nbytes >= 1000 and i < len(suffixes) - 1:
        nbytes /= 1000.0
        i += 1
    f = ("%.2f" % nbytes).rstrip("0").rstrip(".")
    return "%s%s" % (f, suffixes[i])
