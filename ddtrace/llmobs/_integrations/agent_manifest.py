"""Value coercion shared by integrations that build an agent manifest."""

import math
import types
from typing import Any
from typing import Union
from typing import get_args
from typing import get_origin


# Bounds this function's own recursion, not the payload. metadata is a caller dict, and nesting it
# past the interpreter's limit raises RecursionError here. The span sanitizer truncates deep values
# too, but it runs after this and so cannot prevent that.
MAX_WIRE_DEPTH = 20

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

    The allowlist protects the key, this protects the value: a nested blob is the shape a credential
    travels in.
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


def put_field(fields: dict[str, Any], name: str, value: Any) -> None:
    """Assign an optional field, dropping values that mean "not configured". False and 0 are kept."""
    if value is None:
        return
    if isinstance(value, (str, bytes, list, tuple, dict, set, frozenset)) and len(value) == 0:
        return
    fields[name] = value


def is_number(value: Any) -> bool:
    """A finite JSON number. bool is an int subclass, so True would otherwise pass as a count."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    # isfinite on floats only: an int is never non-finite, and converting a huge one would raise.
    return math.isfinite(value) if isinstance(value, float) else True


def wire_value(value: Any, depth: int = 0, ancestors: tuple[int, ...] = ()) -> Any:
    """Coerce a config value to a JSON-native one, or None when it cannot ship."""
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
        items = [wire_value(item, depth + 1, ancestors) for item in value]
        return None if any(item is None for item in items) else items
    if isinstance(value, dict):
        coerced: dict[str, Any] = {}
        for key, item in value.items():
            wired = wire_value(item, depth + 1, ancestors)
            if wired is not None:
                coerced[str(key)] = wired
        return coerced or None
    return None
