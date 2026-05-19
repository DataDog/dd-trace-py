"""Shared helpers for AI Guard provider listeners.

Kept deliberately small: only utilities used by more than one provider
module belong here. Provider-specific schema handling stays in the
respective ``_openai`` / ``_anthropic`` modules.
"""

from collections.abc import Mapping
import threading
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
    shapes resolve correctly — strict ``dict`` would silently drop them.
    """
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


# AIDEV-NOTE: Compound abort-error classes are built lazily on first block.
# Both ``_openai.py`` and ``_anthropic.py`` are imported unconditionally by
# the AI Guard listener, but the provider SDKs are optional runtime
# dependencies — eagerly importing them would break AI Guard initialisation
# in environments that only use a different provider.
#
# The cache uses double-checked locking: the unsynchronised check-then-act
# pattern would let two threads simultaneously enter the build branch and
# each create their own class, so callers that captured ``type(err)`` from
# one block would silently stop matching errors from a concurrent block.
_compound_abort_error_cache: dict[str, type[AIGuardAbortError]] = {}
_compound_abort_error_lock = threading.Lock()


def build_compound_abort_error_cls(
    provider_name: str,
    sdk_error_cls: type[Exception],
    provider_module: Optional[str] = None,
) -> type[AIGuardAbortError]:
    """Return a cached compound abort-error class for *provider_name*.

    The class subclasses both *sdk_error_cls* (the provider SDK's 422
    ``UnprocessableEntityError`` — "policy rejection" semantics used by AI
    Gateway) and ``AIGuardAbortError`` so existing Datadog-specific handlers
    keep working.

    AIDEV-NOTE: catchability asymmetry vs plain ``AIGuardAbortError``.
    ``AIGuardAbortError`` derives from ``DDBlockException(BaseException)``
    so a generic ``except Exception:`` does NOT catch it (by design — a
    block decision must not be silently swallowed). However, the compound
    subclass *also* inherits from the SDK's ``UnprocessableEntityError``,
    which is ``Exception``-derived, so via MRO this subclass IS catchable
    by ``except Exception:``. That asymmetry is intentional: the provider
    contrib must keep ``except <sdk>.APIError:`` blocks working unchanged
    for users migrating from non-AI-Guard error handling. Code that wants
    uniform block detection across providers should branch on
    ``isinstance(e, AIGuardAbortError)``.

    ``provider_module`` preserves the historical module name for telemetry
    fields derived from ``exc_type.__module__`` (notably span ``error.type``)
    after moving the implementation into this shared helper.
    """
    cached = _compound_abort_error_cache.get(provider_name)
    if cached is not None:
        return cached

    with _compound_abort_error_lock:
        cached = _compound_abort_error_cache.get(provider_name)
        if cached is not None:
            return cached

        class CompoundAIGuardAbortError(sdk_error_cls, AIGuardAbortError):  # type: ignore[misc,valid-type]
            def __init__(
                self,
                action: str,
                reason: str,
                tags: Any = None,
                sds: Any = None,
                tag_probs: Any = None,
            ) -> None:
                self.action = action
                self.reason = reason
                self.tags = tags
                self.sds = sds or []
                self.tag_probs = tag_probs

                message = f"AIGuardAbortError(action='{action}', reason='{reason}', tags='{tags}')"
                # AIDEV-NOTE: We can't call the SDK ``UnprocessableEntityError.__init__``
                # here — its MRO super() chain ends up at ``AIGuardAbortError.__init__``
                # (which requires ``(action, reason)``), not ``Exception.__init__``,
                # so the ``super().__init__(message)`` deep in the SDK's ``APIError``
                # raises ``TypeError: missing 'reason'``. Initialize the SDK-side
                # attributes directly instead. The block originates from AI Guard,
                # not an HTTP call, so ``response`` / ``request`` / ``request_id``
                # are ``None``; only ``status_code`` (422) and ``body`` (the AI
                # Guard decision payload) carry semantic meaning.
                self.message = message
                self.request = None
                self.body = {"action": action, "reason": reason, "source": "datadog_ai_guard"}
                self.code = "ai_guard_block"
                self.param = None
                self.type = "ai_guard_abort"
                self.response = None
                self.status_code = 422
                self.request_id = None
                Exception.__init__(self, message)

        CompoundAIGuardAbortError.__name__ = f"{provider_name}AIGuardAbortError"
        CompoundAIGuardAbortError.__qualname__ = CompoundAIGuardAbortError.__name__
        if provider_module is not None:
            CompoundAIGuardAbortError.__module__ = provider_module
        _compound_abort_error_cache[provider_name] = CompoundAIGuardAbortError
        return CompoundAIGuardAbortError


def wrap_abort_error(cause: AIGuardAbortError, compound_cls: Optional[type[AIGuardAbortError]]) -> AIGuardAbortError:
    """Wrap *cause* into the provider-specific compound abort error class.

    Returns *cause* unchanged when *compound_cls* is ``None`` (provider SDK
    not importable) — the listener still surfaces a block, just without
    SDK-hierarchy compatibility.
    """
    if compound_cls is None:
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
