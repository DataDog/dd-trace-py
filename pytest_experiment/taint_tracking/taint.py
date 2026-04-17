from __future__ import annotations

from contextvars import ContextVar
import weakref


class _TaintEntry:
    __slots__ = ("nodeid", "weakref", "obj_type")
    weakref: weakref.ref[object] | None

    def __init__(self, obj: object, nodeid: str, use_weakref: bool) -> None:
        self.nodeid = nodeid
        self.obj_type = type(obj)
        if use_weakref:
            self.weakref = weakref.ref(obj)
        else:
            self.weakref = None

    def is_alive(self) -> bool:
        if self.weakref is not None:
            return self.weakref() is not None
        return True


def _supports_weakref(obj: object) -> bool:
    try:
        weakref.ref(obj)
        return True
    except TypeError:
        return False


_taint_registry: dict[int, _TaintEntry] = {}
_current_test: ContextVar[str | None] = ContextVar("_current_test", default=None)


def set_current_test(nodeid: str | None) -> None:
    _current_test.set(nodeid)


def get_current_test() -> str | None:
    return _current_test.get()


def taint(obj: object) -> None:
    current = _current_test.get()
    if current is None:
        return

    obj_id = id(obj)

    if obj_id in _taint_registry:
        return

    entry = _TaintEntry(obj, current, _supports_weakref(obj))
    _taint_registry[obj_id] = entry


def get_taint(obj: object) -> str | None:
    obj_id = id(obj)
    entry = _taint_registry.get(obj_id)

    if entry is None:
        return None

    if not entry.is_alive():
        del _taint_registry[obj_id]
        return None

    if entry.obj_type is not type(obj):
        return None

    return entry.nodeid


def clear() -> None:
    _taint_registry.clear()
