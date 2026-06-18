from typing import Any
from typing import Union

from ddtrace.internal._unpatched import threading_Lock


DDWafRulesType = Union[None, int, str, list[Any], dict[str, Any]]


# AIDEV-NOTE: these capsules wrap the native libddwaf wrappers
# (``ddtrace.internal.native._native.ddwaf``). The native objects free their underlying libddwaf
# resource in their Rust ``Drop`` impl when garbage-collected, so the capsules only need to hold a
# reference (and any Python-side metadata/locks) — there is no explicit free callback anymore.


class ddwaf_handle_capsule:
    def __init__(self, handle: Any) -> None:
        self.handle = handle

    def __bool__(self) -> bool:
        return bool(self.handle)


class ddwaf_context_capsule:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.rc_products: str = ""
        # Serializes concurrent ddwaf_context_eval calls on the same context: the native binding
        # releases the GIL during eval, so thread-pool workers and the event loop can hit a shared
        # context at once, which libddwaf does not allow.
        self._lock: threading_Lock = threading_Lock()

    def __bool__(self) -> bool:
        return bool(self.ctx)


class ddwaf_subcontext_capsule:
    # Subcontexts (libddwaf 2.0) evaluate non-persisting RASP data, inheriting the parent
    # context's persistent data. In v2.0 a subcontext is independent of its parent context (the
    # context may be destroyed first; shared objects are reference counted), so we don't need to
    # keep a reference to the parent. Own lock to serialize eval/destroy on the same subcontext.
    def __init__(self, subctx: Any) -> None:
        self.subctx = subctx
        self._lock: threading_Lock = threading_Lock()

    def __bool__(self) -> bool:
        return bool(self.subctx)


class ddwaf_builder_capsule:
    def __init__(self, builder: Any) -> None:
        self.builder = builder

    def __bool__(self) -> bool:
        return bool(self.builder)
