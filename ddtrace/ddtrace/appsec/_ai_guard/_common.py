"""Shared helpers for AI Guard provider listeners.

Kept deliberately small: only utilities used by more than one provider
module belong here. Provider-specific schema handling stays in the
respective ``_openai`` / ``_anthropic`` modules.
"""

from collections.abc import Mapping
from typing import Any

from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read *key* from *obj* whether it's a mapping or an object.

    Uses ``Mapping`` (not just ``dict``) so typed content parts wrapped in
    ``MappingProxyType`` / ``UserDict`` / Pydantic mapping protocol resolve
    correctly -- a strict ``dict`` check would silently drop them.
    """
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def wrap_abort_error(cause: AIGuardAbortError, compound_cls: type[AIGuardAbortError]) -> AIGuardAbortError:
    """Wrap *cause* into the provider-specific compound abort error class.

    Returns *cause* unchanged when *compound_cls* is bare ``AIGuardAbortError``
    (provider SDK not importable): wrapping a class into itself would only
    produce a redundant clone whose ``__cause__`` is the original.
    """
    if compound_cls is AIGuardAbortError:
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
