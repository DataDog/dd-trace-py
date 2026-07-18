from typing import Any  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Protocol  # noqa:F401
from typing import Union  # noqa:F401


class ArgumentError(Exception):
    """
    This is raised when an argument lookup, either by position or by keyword, is
    not found.
    """


def get_argument_value(
    args: Union[tuple[Any], list[Any]],
    kwargs: dict[str, Any],
    pos: int,
    kw: str,
    optional: bool = False,
) -> Optional[Any]:
    """
    This function parses the value of a target function argument that may have been
    passed in as a positional argument or a keyword argument. Because monkey-patched
    functions do not define the same signature as their target function, the value of
    arguments must be inferred from the packed args and kwargs.
    Keyword arguments are prioritized, followed by the positional argument. If the
    argument cannot be resolved, an ``ArgumentError`` exception is raised, which could
    be used, e.g., to handle a default value by the caller.
    :param args: Positional arguments
    :param kwargs: Keyword arguments
    :param pos: The positional index of the argument if passed in as a positional arg
    :param kw: The name of the keyword if passed in as a keyword argument
    :return: The value of the target argument
    """
    try:
        return kwargs[kw]
    except KeyError:
        try:
            return args[pos]
        except IndexError:
            if optional:
                return None
            raise ArgumentError("%s (at position %d)" % (kw, pos))


def set_argument_value(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    pos: int,
    kw: str,
    value: Any,
    override_unset: bool = False,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """
    Returns a new args, kwargs with the given value updated
    :param args: Positional arguments
    :param kwargs: Keyword arguments
    :param pos: The positional index of the argument
    :param kw: The name of the keyword
    :param value: The new value of the target argument
    :return: Updated args and kwargs
    """
    if len(args) > pos:
        args = args[:pos] + (value,) + args[pos + 1 :]
    elif kw in kwargs or override_unset:
        kwargs[kw] = value
    else:
        raise ArgumentError("%s (at position %d) is invalid" % (kw, pos))

    return args, kwargs


def _get_metas_to_propagate(context: Any) -> list[tuple[str, str]]:
    if context is None:
        return []
    meta = context._meta
    if not meta:
        return []
    # PERF: this runs once per child span, but _meta is shared by reference across a local
    # trace (Context.copy) and its _dd.p.* subset is stable within a trace. When _meta tracks
    # a version (_VersionedMeta), cache the filtered list keyed on that version so the filter
    # runs once per trace instead of once per span. Any mutation bumps _v (including in-place
    # _dd.p.dm changes from a sampling override), so the cache invalidates by construction.
    # A plain dict (remote/propagation context) has no _v -> recompute, unchanged behavior.
    version = getattr(meta, "_v", None)
    if version is not None:
        cached = meta._prop_cache
        if cached is not None and cached[0] == version:
            return cached[1]
    # Snapshot items to avoid "dictionary changed size during iteration" when
    # context._meta is mutated by another thread (e.g. tracer.sample()). See #16523.
    # PERF: reuse the (k, v) tuples the snapshot already built instead of rebuilding them.
    result = [item for item in list(meta.items()) if isinstance(item[0], str) and item[0].startswith("_dd.p.")]
    if version is not None:
        meta._prop_cache = (version, result)
    return result


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
