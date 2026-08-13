"""Value coercion shared by integrations that build an agent manifest."""

import math
import types
from typing import Any
from typing import Optional
from typing import TypeVar
from typing import Union
from typing import cast
from typing import get_args
from typing import get_origin


# Bounds this function's own recursion, not the payload. metadata is a caller dict, and nesting it
# past the interpreter's limit raises RecursionError here. The span sanitizer truncates deep values
# too, but it runs after this and so cannot prevent that.
MAX_WIRE_DEPTH = 20

# Depth alone does not bound the work. A dict whose children are shared expands into a tree, so 20
# levels of sharing is 2**20 emitted nodes built from 20 dicts in memory. Cycle detection cannot
# catch that, because a shared child is a legitimate second visit rather than an ancestor.
MAX_WIRE_NODES = 10_000

# AIDEV-NOTE: allowlist, not denylist. model_settings is the one field whose key set the caller
# controls, and the dangerous keys are provider-specific by name (extra_headers, openai_user,
# xai_user), so only a closed list of generic inference parameters is safe. Widening it is a
# security decision.
ALLOWED_MODEL_SETTINGS_KEYS = frozenset(
    {
        "frequency_penalty",
        "logit_bias",
        "logprobs",
        "max_tokens",
        "parallel_tool_calls",
        "presence_penalty",
        "seed",
        "stop_sequences",
        "temperature",
        "timeout",
        "tool_choice",
        "top_k",
        "top_logprobs",
        "top_p",
    }
)


def callable_name(fn: Any) -> str:
    """Best recoverable name for a callable. Two lambdas both report <lambda>, as Python does."""
    return getattr(fn, "__name__", None) or getattr(getattr(fn, "func", None), "__name__", None) or type(fn).__name__


def type_name(candidate: Any) -> str:
    """Readable name for a declared type, such as list[Fruit].

    Assembled from the type's parts because str() qualifies each argument with its defining module.
    """
    if candidate is type(None):
        return "None"
    origin, args = get_origin(candidate), get_args(candidate)
    if origin is None or not args:
        return getattr(candidate, "__name__", None) or str(candidate)
    names = [type_name(arg) for arg in args]
    if origin is Union or origin is getattr(types, "UnionType", None):
        return " | ".join(names)
    return "{}[{}]".format(getattr(origin, "__name__", None) or str(origin), ", ".join(names))


def is_flat_scalar_value(value: Any) -> bool:
    """True for a JSON scalar, a flat list of scalars, or a flat mapping of scalars.

    No allowlisted setting nests in its declared type, so this bounds a shape that should not
    arrive: model_settings is a TypedDict, nothing validates it at run time, and wire_value would
    coerce a nested value and pass it through rather than drop it.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(item is None or isinstance(item, (str, int, float, bool)) for item in value)
    if isinstance(value, dict):
        # Numeric only: logit_bias is token id to bias, so a string there is already invalid.
        return all(
            isinstance(key, (str, int)) and isinstance(item, (int, float)) and not isinstance(item, bool)
            for key, item in value.items()
        )
    return False


T = TypeVar("T")


def prune_empty(node: T) -> T:
    """Drop every value that means "not configured", depth-first. 0, 0.0 and False are kept.

    Runs once over a finished manifest so a section can assign a field without guarding it, and so a
    container emptied by its own children drops too. The cast is internal: the walk rebuilds plain
    containers, and the caller's type is preserved by construction.
    """
    if isinstance(node, dict):
        kept: dict[Any, Any] = {}
        for key, value in node.items():
            pruned = prune_empty(value)
            if pruned is None:
                continue
            if isinstance(pruned, (str, bytes, list, tuple, dict, set, frozenset)) and len(pruned) == 0:
                continue
            kept[key] = pruned
        return cast(T, kept)
    if isinstance(node, list):
        return cast(T, [prune_empty(item) for item in node])
    return node


def is_number(value: Any) -> bool:
    """A finite JSON number. bool is an int subclass, so True would otherwise ship as true here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    # isfinite on floats only: an int is never non-finite, and converting a huge one would raise.
    return math.isfinite(value) if isinstance(value, float) else True


def wire_value(value: Any, depth: int = 0, ancestors: tuple[int, ...] = (), budget: Optional[list[int]] = None) -> Any:
    """Coerce a config value to a JSON-native one, or None when it cannot ship.

    A dropped entry costs its whole list but only its own key in a mapping. Compacting a list would
    shift the surviving indices, and an ordered field such as memory_policies then describes a
    pipeline the agent does not run, so omitting the field beats misreporting it.

    budget is internal: a single-element list counting nodes still allowed across the whole walk.
    """
    if budget is None:
        budget = [MAX_WIRE_NODES]
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # NaN and Infinity encode as bare tokens that are not valid JSON.
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple, dict)):
        if depth > MAX_WIRE_DEPTH or id(value) in ancestors:
            return None
        ancestors = ancestors + (id(value),)
    if isinstance(value, (list, tuple)):
        items = [wire_value(item, depth + 1, ancestors, budget) for item in value]
        return None if any(item is None for item in items) else items
    if isinstance(value, dict):
        coerced: dict[str, Any] = {}
        for key, item in value.items():
            wired = wire_value(item, depth + 1, ancestors, budget)
            if wired is not None:
                coerced[str(key)] = wired
        return coerced or None
    return None
