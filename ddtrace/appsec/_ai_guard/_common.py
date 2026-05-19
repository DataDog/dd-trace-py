"""Shared helpers for AI Guard provider listeners.

Kept deliberately small: only utilities used by more than one provider
module belong here. Provider-specific schema handling stays in the
respective ``_openai`` / ``_anthropic`` modules.
"""

from collections.abc import Mapping
from typing import Any
from typing import Optional

from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a mapping or object attribute.

    Provider SDKs deliver request-side payloads as user-supplied dicts /
    mapping wrappers (``MappingProxyType``, ``UserDict``, Pydantic models
    exposing mapping protocol) and response-side payloads as SDK model
    objects with attributes. ``Mapping`` (not just ``dict``) is accepted on
    the mapping branch so typed content parts wrapped in any of those
    shapes resolve correctly -- strict ``dict`` would silently drop them.
    """
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def wrap_abort_error(cause: AIGuardAbortError, compound_cls: Optional[type[AIGuardAbortError]]) -> AIGuardAbortError:
    """Wrap *cause* into the provider-specific compound abort error class.

    Returns *cause* unchanged when *compound_cls* is ``None`` or already the
    bare ``AIGuardAbortError`` class (provider SDK not importable) -- the
    listener still surfaces a block, just without SDK-hierarchy
    compatibility. Skipping the wrap in that case avoids producing a
    redundant ``AIGuardAbortError`` whose ``__cause__`` is another
    ``AIGuardAbortError`` with identical fields.
    """
    if compound_cls is None or compound_cls is AIGuardAbortError:
        return cause
    wrapped = compound_cls(
        action=cause.action,
        reason=cause.reason,
        tags=cause.tags,
        sds=cause.sds,
        tag_probs=cause.tag_probs,
    )
    wrapped.__cause__ = cause
    return wrapped
