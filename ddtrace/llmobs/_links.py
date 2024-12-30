import inspect
from typing import Optional


_object_span_links = {}
_object_relationships = {}


def track_object_interactions(frame, event, arg):
    """Track method calls and object interactions."""
    if event != "call":
        return track_object_interactions

    # Get function details
    source_obj = frame.f_locals.get("self")
    method_name = frame.f_code.co_name

    # Track object creation or modification
    try:
        # Capture arguments that might involve object relationships
        args = inspect.getargvalues(frame)

        # Look for potential object interactions
        for arg_name in args.args:
            if arg_name == "self":
                continue
            incoming_obj = frame.f_locals.get(arg_name)

            # Check for object creation or modification
            if method_name in [
                "__init__",
                "__new__",
                "__add__",
                "append",
                "extend",
                "update",
            ]:
                _record_relationship(source_obj, incoming_obj, f"{method_name} operation")
    except Exception as e:
        print("Error capturing object interactions ", e)

    return track_object_interactions


class TrackedStr(str):
    def __add__(self, other):
        result = super().__add__(other)
        result = TrackedStr(result)
        _record_relationship(result, other, "__add__")
        _record_relationship(result, self, "__add__")
        return result

    def __radd__(self, other):
        result = super().__radd__(other)
        result = TrackedStr(result)
        _record_relationship(result, other, "__radd__")
        _record_relationship(result, self, "__radd__")
        return result

    def format(self, *args, **kwargs):
        result = super().format(*args, **kwargs)
        result = TrackedStr(result)
        for arg in args:
            _record_relationship(result, arg, "format")
        for _, value in kwargs.items():
            _record_relationship(result, value, "format")
        _record_relationship(result, self, "format")
        return result

    def split(self, *args, **kwargs):
        return super().split(*args, **kwargs)

    def join(self, *args, **kwargs):
        return super().join(*args, **kwargs)


class TrackedList(list):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for item in self:
            _record_relationship(self, item, "__init__")

    def append(self, item):
        result = super().append(item)
        _record_relationship(self, item, "append")
        return result

    def extend(self, iterable):
        result = super().extend(iterable)
        _record_relationship(self, iterable, "extend")
        return result

    def __delitem__(self, key) -> None:
        if key < len(self):
            _remove_relationship(self, self[key], "__delitem__")
        super().__delitem__(key)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        _record_relationship(self, value, "__setitem__")

    def __add__(self, other):
        result = super().__add__(other)
        _record_relationship(self, other, "__add__")
        return result


class TrackedDict(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, value in self.items():
            _record_relationship(self, key, "__init__")
            _record_relationship(self, value, "__init__")

    def fromkeys(self, *args, **kwargs):
        ret = super().fromkeys(*args, **kwargs)
        for key, value in ret.items():
            _record_relationship(ret, key, "fromkeys")
            _record_relationship(ret, value, "fromkeys")
        return ret

    def update(self, *args, **kwargs):
        result = super().update(*args, **kwargs)
        for arg in args:
            _record_relationship(self, arg, "update")
        return result

    def pop(self, key, *args, **kwargs):
        val = super().pop(key, *args, **kwargs)
        _remove_relationship(self, key, "pop")
        if val is not None:
            _remove_relationship(self, val, "pop")
        return val

    def __delitem__(self, key) -> None:
        val = self.get(key)
        super().__delitem__(key)
        _remove_relationship(self, key, "__delitem__")
        if val is not None:
            _remove_relationship(self, val, "__delitem__")

    def __setitem__(self, key, value):
        if key in self:
            _remove_relationship(self, self[key], "__setitem__")
        super().__setitem__(key, value)
        _record_relationship(self, value, "__setitem__")
        _record_relationship(self, key, "__setitem__")


def _remove_relationship(source_obj, incoming_obj, operation):
    if source_obj is None or incoming_obj is None:
        return
    if source_obj not in _object_relationships:
        _object_relationships[get_object_id(source_obj)] = set()
    _object_relationships[get_object_id(source_obj)].remove(get_object_id(incoming_obj))


def _record_relationship(source_obj, incoming_obj, operation):
    if source_obj is None or incoming_obj is None:
        return
    if get_object_id(source_obj) not in _object_relationships:
        _object_relationships[get_object_id(source_obj)] = set()
    _object_relationships[get_object_id(source_obj)].add(get_object_id(incoming_obj))


def get_object_id(obj):
    return f"{type(obj).__name__}_{id(obj)}"


def add_span_links_to_object(obj, span_links):
    obj_id = get_object_id(obj)
    if obj_id not in _object_span_links:
        _object_span_links[obj_id] = []
    _object_span_links[obj_id] += span_links


def get_span_links_from_object(obj):
    return _object_span_links.get(get_object_id(obj), [])


def search_links_from_relationships(obj_id, visited: Optional[set] = None, links: Optional[list] = None):
    if visited is None:
        visited = set()
    visited.add(obj_id)
    if links is None:
        links = []
    if obj_id not in _object_relationships:
        return _object_span_links.get(obj_id, [])
    for obj in _object_relationships[obj_id]:
        if obj not in visited:
            links += search_links_from_relationships(obj, visited, links)
    return _object_span_links.get(obj_id, [])
