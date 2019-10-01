import collections
import sys
from itertools import chain

_UNSET = object()


def iter_object(o):
    if hasattr(o, '__slots__'):
        return (
            s
            for s in (getattr(o, slot, _UNSET) for slot in o.__slots__)
            if s != _UNSET
        )
    elif hasattr(o, '__dict__'):
        return list(o.__dict__.items())
    elif isinstance(o, dict):
        # Make a copy to avoid corruption
        return chain.from_iterable(list(o.items()))
    elif isinstance(o, (list, set, frozenset, tuple, collections.deque)):
        # Make a copy to avoid corruption
        return iter(list(o))
    return []


def sizeof(o):
    """Returns the approximate memory footprint an object and all of its contents."""
    seen = set()

    def _sizeof(o):
        # do not double count the same object
        if id(o) in seen:
            return 0
        seen.add(id(o))
        return sys.getsizeof(o) + sum(map(_sizeof, iter_object(o)))

    return _sizeof(o)
