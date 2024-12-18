from typing import Dict
from typing import Text
from typing import Union

from ddtrace.internal.compat import NumericType
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_TagNameType = Union[Text, bytes]
_MetaDictType = Dict[_TagNameType, Text]
_MetricDictType = Dict[_TagNameType, NumericType]


class _ImmutableDict(dict):
    def __setitem__(self, name, value):
        # log a warning when trying to set an attribute on an immutable map, do not raise a ValueError
        log.warning("Map is immutable, ignoring set attribute %s and value %r", name, value)


class _ImmutableList(list):
    def __setitem__(self, name, value):
        # log a warning when trying to set an attribute on an immutable list, do not raise a ValueError
        log.warning("List is immutable, ignoring set attribute %s and value %r", name, value)

    def append(self, value):
        # log a warning when trying to append to an immutable list, do not raise a ValueError
        log.warning("List is immutable, ignoring append of value %r", value)

    def extend(self, values):
        # log a warning when trying to extend an immutable list, do not raise a ValueError
        log.warning("List is immutable, ignoring extend with iterable %r", values)

    def insert(self, index, value):
        # log a warning when trying to insert into an immutable list, do not raise a ValueError
        log.warning("List is immutable, ignoring insert at index %d with value %r", index, value)

    def remove(self, value):
        # log a warning when trying to remove from an immutable list, do not raise a ValueError
        log.warning("List is immutable, ignoring remove of value %r", value)

    def pop(self, index=-1):
        # log a warning when trying to pop from an immutable list, do not raise a ValueError
        log.warning("List is immutable, ignoring pop at index %d", index)
