from collections import defaultdict
from contextlib import contextmanager
from typing import Dict


class ExecutionContext:
    def __init__(self, identifier=None, parent=None, **kwargs):
        self.identifier = identifier or ExecutionContext.make_identifier()
        self._parents = []
        self._children = []
        if parent is not None:
            self.addParent(parent)
        self._data = self._build_data(kwargs)

    @property
    def parents(self):
        return self._parents

    @property
    def children(self):
        return self._children

    def _build_data(self, dotted_attrs: Dict):
        attr_data = dict()
        for key, value in dotted_attrs.items():
            current_parent_attr = attr_data
            key_parts = key.split(".")
            for key_part in key_parts[:-1]:
                child_attr = dict()
                current_parent_attr[key_part] = child_attr
                current_parent_attr = child_attr
            current_parent_attr[key_parts[-1]] = value

    def end(self):
        dispatch("context.%s.ended" % self.identifier)

    def addParent(self, context):
        if context is root_context:
            raise ValueError("Cannot add parent to root context")
        self._parents.append(context)

    def addChild(self, context):
        self._children.append(context)

    @classmethod
    @contextmanager
    def context_with_data(cls, parent=None, **kwargs):
        new_context = cls(parent=parent, **kwargs)
        try:
            yield new_context
        finally:
            new_context.end()

    @classmethod
    def make_identifier(cls):
        return "foobar.banana"


root_context = ExecutionContext()
current_context = root_context


def context_with_data(parent=None, **kwargs):
    if parent is None:
        parent = current_context
    return ExecutionContext.context_with_data(parent=parent, **kwargs)


def get_item(data_key):
    context = current_context
    result = None
    while result is None:
        result = context._get_item(data_key)
    return result


class EventHub:
    def __init__(self):
        self._listeners = defaultdict(list)

    def has_listeners(self, event_id):
        return event_id in self._listeners

    def on(self, event_id, callback):
        self._listeners[event_id].append(callback)


_event_hub = EventHub()


def has_listeners(event_id):
    return _event_hub.has_listeners(event_id)


def on(event_id, callback):
    return _event_hub.on(event_id, callback)


def dispatch(event_id):
    pass
