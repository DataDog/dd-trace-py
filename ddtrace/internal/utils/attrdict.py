from typing import Any
from typing import Callable

from six.moves.collections_abc import Mapping


class AttrDict(Mapping):
    """Dict-like object that allows for item attribute access

    Example::

       data = AttrDict()
       data['key'] = 'value'
       print(data['key'])

       data.key = 'new-value'
       print(data.key)

       # Convert an existing `dict`
       data = AttrDict(dict(key='value'))
       print(data.key)
    """

    def __init__(self, *args, **kwargs):
        # type: (Any, Any) -> None
        self.__dict__.update(*args, **kwargs)

    def __getattr__(self, name):
        # type: (str) -> Any
        return getattr(self.__dict__, name)

    def __contains__(self, name):  # type: ignore[override]
        # type: (str) -> bool
        return name in self.__dict__

    def __getitem__(self, name):
        # type: (str) -> Any
        return self.__dict__[name]

    def __setitem__(self, name, value):
        # type: (str, Any) -> None
        self.__dict__[name] = value

    def __iter__(self):
        return iter(self.__dict__)

    def __len__(self):
        return len(self.__dict__)


class DefaultAttrDict(AttrDict):
    """AttrDict with a default value for missing keys."""

    def __init__(self, default):
        # type: (Callable[[DefaultAttrDict, str], Any]) -> None
        super(DefaultAttrDict, self).__init__()
        self.__default_attrdict_constructor__ = default

    def __getattr__(self, name):
        # type: (str) -> Any
        try:
            return super(DefaultAttrDict, self).__getattr__(name)
        except AttributeError:
            value = self[name] = self.__default_attrdict_constructor__(self, name)
            return value
