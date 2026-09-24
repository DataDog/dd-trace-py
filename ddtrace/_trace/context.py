from typing import Optional
from weakref import ReferenceType
from weakref import ref

from ddtrace.internal import forksafe
from ddtrace.internal.native._native import Context


__all__ = ["Context"]


# Context equality is trace-level equality rather than object identity, so this side table is
# keyed by id and guarded by a weak reference. It is populated only by the MicroVM refresh path.
_RUNTIME_IDENTITY_GENERATIONS: dict[int, tuple[ReferenceType[Context], int]] = {}
_RUNTIME_IDENTITY_GENERATIONS_LOCK = forksafe.RLock()


def _get_runtime_identity_generation(context: Context) -> Optional[int]:
    # Return the runtime generation that created this context. A copied context can be used after
    # a MicroVM refresh, so this tells the tracer whether it is still safe to create child spans.
    with _RUNTIME_IDENTITY_GENERATIONS_LOCK:
        seen: set[int] = set()
        current: Optional[Context] = context
        while current is not None and id(current) not in seen:
            context_id = id(current)
            seen.add(context_id)
            entry = _RUNTIME_IDENTITY_GENERATIONS.get(context_id)
            if entry is not None:
                context_ref, generation = entry
                if context_ref() is current:
                    return generation
                # The old context was collected and Python may now be using its id for a different object.
                if _RUNTIME_IDENTITY_GENERATIONS.get(context_id) is entry:
                    _RUNTIME_IDENTITY_GENERATIONS.pop(context_id, None)
            current = current._otel_sampling_state_owner
        return None


def _set_runtime_identity_generation(context: Context, generation: int) -> None:
    # Remember which runtime generation created this context so copied contexts cannot create
    # spans after a MicroVM refresh.
    context_id = id(context)

    def remove(_context_ref: ReferenceType[Context]) -> None:
        # Remove the entry when the context is gone, but keep a newer entry if Python reused its id.
        with _RUNTIME_IDENTITY_GENERATIONS_LOCK:
            entry = _RUNTIME_IDENTITY_GENERATIONS.get(context_id)
            if entry is not None and entry[0] is _context_ref:
                _RUNTIME_IDENTITY_GENERATIONS.pop(context_id, None)

    # Contexts compare by trace data rather than object identity, so use the object id and a weak
    # reference instead of a WeakKeyDictionary. This avoids confusing separate contexts or keeping
    # them alive longer than necessary.
    with _RUNTIME_IDENTITY_GENERATIONS_LOCK:
        _RUNTIME_IDENTITY_GENERATIONS[context_id] = (ref(context, remove), generation)
