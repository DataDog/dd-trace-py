"""Primitives shared by every integration that builds an agent manifest.

The manifest is assembled from caller-supplied objects, so the values reaching it are not the
integration's to trust. These helpers exist because an unencodable value does not fail politely: the
span encoder falls back to repr, which can disclose the object, and a bare NaN or Infinity token is
not valid JSON at all. Spans ship batched, so one bad value discards every span batched with it
rather than just its own field.

Framework-specific extraction stays in each integration. What belongs here is the coercion every
manifest needs on the way out.
"""

import math
from typing import Any


# Bump only when the manifest encoding changes. Shared so that every integration reports the same
# shape under the same number, which is the only thing that makes it a useful discriminator.
MANIFEST_VERSION = 1

# Deep enough for a nested output schema, shallow enough that a cyclic value terminates.
MAX_WIRE_DEPTH = 20

# Distinct from None, which is a legal value inside a JSON document. wire_value conflates the two
# because for a config field a null and an absent key mean the same thing.
UNENCODABLE = object()


def put_field(fields: dict[str, Any], name: str, value: Any) -> None:
    """Assign an optional manifest field, dropping values that mean "not configured".

    A null and an absent key are indistinguishable to a consumer, so shipping one makes "the caller
    did not set this" unreadable. False and 0 are kept: filtering on truthiness instead is what loses
    a configured temperature=0. Fields that are always present, such as framework, are assigned
    directly rather than through here.
    """
    if value is None:
        return
    if isinstance(value, (str, bytes, list, tuple, dict, set, frozenset)) and len(value) == 0:
        return
    fields[name] = value


def is_number(value: Any) -> bool:
    """A finite JSON number.

    bool is an int subclass, so True would otherwise pass as a count. A non-finite float is rejected
    for the same reason wire_value rejects one: it encodes as a bare Infinity or NaN token, which is
    not valid JSON, and spans ship batched, so one of them invalidates the whole payload rather than
    this one field. A timeout of float("inf") is a plausible way to say "no timeout".

    isfinite is applied only to floats: a Python int is never non-finite, and converting a very large
    one to float to check would raise.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) if isinstance(value, float) else True


def wire_text(value: Any) -> Any:
    """A caller-supplied string, or None when it is not one.

    Frameworks type these as str without enforcing it, so an object arrives intact and the encoder
    prints its repr onto the span. Dropping is the safe direction: a missing description costs
    nothing, a repr can carry credentials.
    """
    return value if isinstance(value, str) else None


def wire_value(value: Any, depth: int = 0, ancestors: tuple[int, ...] = ()) -> Any:
    """Coerce a config value to a JSON-native one, or None when it cannot ship.

    An allowlist rather than a repr fallback: an unencodable value in meta_struct fails the span at
    encode time, and a repr can leak connection config. A provider sentinel such as OpenAI's Omit
    means "not set", so it drops rather than emitting "Omit()" where a number belongs.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # json.dumps writes these as bare NaN/Infinity tokens, which are not valid JSON.
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple, dict)):
        # A self-referential container would recurse until RecursionError, costing the section.
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
                # Coerce, not drop: a mapping such as logit_bias is keyed by token id, and dropping
                # the key loses the field.
                coerced[str(key)] = wired
        return coerced or None
    return None


def wire_document(value: Any, depth: int = 0, ancestors: tuple[int, ...] = ()) -> Any:
    """Coerce a whole JSON document, keeping null as data rather than treating it as absence.

    For a config field a null means "not configured" and wire_value is right to drop it. A declared
    JSON Schema is a document, where "default": null and an enum containing null are assertions the
    caller made. Dropping those silently narrows the recorded contract: an enum of ["a", "b", null]
    would otherwise lose the whole list and record the field as unconstrained.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else UNENCODABLE
    if isinstance(value, (list, tuple, dict)):
        if depth > MAX_WIRE_DEPTH or id(value) in ancestors:
            return UNENCODABLE
        ancestors = ancestors + (id(value),)
    if isinstance(value, (list, tuple)):
        items = [wire_document(item, depth + 1, ancestors) for item in value]
        return UNENCODABLE if any(item is UNENCODABLE for item in items) else items
    if isinstance(value, dict):
        coerced: dict[str, Any] = {}
        for key, item in value.items():
            wired = wire_document(item, depth + 1, ancestors)
            if wired is not UNENCODABLE:
                coerced[str(key)] = wired
        return coerced
    return UNENCODABLE


def wire_schema(value: Any) -> Any:
    """wire_document for a put_field call site: an unencodable document drops the key entirely."""
    wired = wire_document(value)
    return None if wired is UNENCODABLE else wired
