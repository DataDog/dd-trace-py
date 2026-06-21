from typing import Any
from typing import Union

from ddtrace.internal._unpatched import threading_Lock


DDWafRulesType = Union[None, int, str, list[Any], dict[str, Any]]


# AIDEV-NOTE: these capsules wrap the high-level native libddwaf objects
# (``ddtrace.internal.native._native.ddwaf`` Builder/Handle/Context/Subcontext). The native objects
# own their libddwaf resource and free it in their Rust ``Drop`` impl when garbage-collected, so the
# capsules only hold a reference plus any Python-side metadata/locks. A wrapped value is either a
# native object (always truthy) or ``None`` (e.g. a failed build / subcontext init).


class ddwaf_handle_capsule:
    def __init__(self, handle: Any) -> None:
        self.handle = handle

    def __bool__(self) -> bool:
        return self.handle is not None


class ddwaf_context_capsule:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.rc_products: str = ""
        # Serializes concurrent run() calls on the same context: the native binding releases the GIL
        # during eval, so thread-pool workers and the event loop can hit a shared context at once,
        # which libddwaf does not allow.
        self._lock: threading_Lock = threading_Lock()

    def __bool__(self) -> bool:
        return self.ctx is not None


class ddwaf_subcontext_capsule:
    # Subcontexts (libddwaf 2.0) evaluate non-persisting RASP data, inheriting the parent context's
    # persistent data. A subcontext is independent of its parent (the context may be destroyed first;
    # shared objects are reference counted), so we don't keep a reference to the parent. Own lock to
    # serialize eval on the same subcontext.
    def __init__(self, subctx: Any) -> None:
        self.subctx = subctx
        self._lock: threading_Lock = threading_Lock()

    def __bool__(self) -> bool:
        return self.subctx is not None


class ddwaf_builder_capsule:
    def __init__(self, builder: Any) -> None:
        self.builder = builder

    def __bool__(self) -> bool:
        return self.builder is not None
